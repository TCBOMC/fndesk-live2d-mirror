#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fndesk Live2D 资源站镜像同步脚本（标准库实现，无第三方依赖）。

负责把上游资源站（默认 https://fndesk.roceos.net:4433）的
「模型列表 + 模型文件 + 缩略图 + 皮肤页 + 台词语音」完整镜像到本地目录，
路径结构与上游完全一致，因此现有 Fndesk Live2D 组件无需任何改动即可指向本镜像。

环境变量：
  SOURCE_HOST        上游资源站地址（默认 https://fndesk.roceos.net:4433）
  WEBROOT            本地镜像根目录（默认 /data/mirror）
  SYNC_WORKERS       并发下载线程数（默认 6）
  SYNC_VOICES        是否同步台词语音（/data/ships + /azurlane/*.ogg），默认 false
  SYNC_VOICE_LANGS   语音数据语言，逗号分隔：CN,EN,JP,KR,TW（默认 CN）
  SYNC_LIMIT         最多同步模型数（0=全部），调试/部分镜像用（默认 0）
  SYNC_FORCE         true 时忽略本地缓存强制重下（默认 false）
  SYNC_INSECURE      true 时关闭 TLS 证书校验（默认 false）
  HTTPS_PROXY/HTTP_PROXY  同步时走代理（容器内访问外网用）
"""
import os
import re
import json
import time
import ssl
import sys
import urllib.request
import urllib.error
import concurrent.futures

SOURCE_HOST = os.environ.get("SOURCE_HOST", "https://fndesk.roceos.net:4433").rstrip("/")
WEBROOT = os.environ.get("WEBROOT", "/data/mirror")
WORKERS = int(os.environ.get("SYNC_WORKERS", "6") or "6")
SYNC_VOICES = os.environ.get("SYNC_VOICES", "false").lower() in ("1", "true", "yes", "on")
VOICE_LANGS = [x.strip().upper() for x in os.environ.get("SYNC_VOICE_LANGS", "CN").split(",") if x.strip()]
SYNC_FORCE = os.environ.get("SYNC_FORCE", "false").lower() in ("1", "true", "yes", "on")
SYNC_LIMIT = int(os.environ.get("SYNC_LIMIT", "0") or "0")
INSECURE = os.environ.get("SYNC_INSECURE", "false").lower() in ("1", "true", "yes", "on")

# 路径推导（与组件 fnos-live2d 内部的常量一致）
L2D_BASE = SOURCE_HOST + "/azurlane/live2d"   # 模型根：{host}/azurlane/live2d
VOICE_BASE = SOURCE_HOST + "/azurlane"        # 语音根：{host}/azurlane/{voicePath}.ogg
DATA_BASE = SOURCE_HOST + "/data/ships"       # 游戏数据：{host}/data/ships/{lang}/{gid}.json

# ---------- HTTP ----------
ssl_ctx = ssl.create_default_context()
if INSECURE:
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

_proxies = {}
if os.environ.get("HTTPS_PROXY"):
    _proxies["https"] = os.environ["HTTPS_PROXY"]
if os.environ.get("HTTP_PROXY"):
    _proxies["http"] = os.environ["HTTP_PROXY"]
_opener = urllib.request.build_opener(urllib.request.ProxyHandler(_proxies))


def fetch_bytes(rel_url, conditional_mtime=None, retries=2):
    """下载一个相对路径资源。返回 (data, last_modified)。
    404 -> (None, None)；304 未修改 -> (None, last_modified_str)。"""
    url = SOURCE_HOST + rel_url
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Fndesk-Mirror)"})
    if conditional_mtime and not SYNC_FORCE:
        req.add_header("If-Modified-Since",
                       time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(conditional_mtime)))
    last_err = None
    for _ in range(retries + 1):
        try:
            resp = _opener.open(req, timeout=60)
            data = resp.read()
            return data, resp.headers.get("Last-Modified")
        except urllib.error.HTTPError as e:
            if e.code in (304, 404):
                return None, e.headers.get("Last-Modified")
            last_err = e
            time.sleep(1)
        except (urllib.error.URLError, ConnectionError, TimeoutError, ssl.SSLError) as e:
            last_err = e
            time.sleep(1)
    raise last_err


def get_text(rel_url):
    data, _ = fetch_bytes(rel_url)
    return data.decode("utf-8", "replace") if data else ""


def local_of(rel_url):
    return os.path.join(WEBROOT, rel_url.lstrip("/"))


def save_local(rel_url, data, last_modified=None):
    path = local_of(rel_url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    if last_modified:
        try:
            t = time.mktime(time.strptime(last_modified, "%a, %d %b %Y %H:%M:%S GMT"))
            os.utime(path, (t, t))
        except Exception:
            pass


def download(rel_url):
    """下载静态资源（相对路径）。返回 True=本次新下载，False=跳过/404/304。"""
    path = local_of(rel_url)
    mtime = (os.path.getmtime(path)
             if (not SYNC_FORCE and os.path.exists(path) and os.path.getsize(path) > 0)
             else None)
    data, lm = fetch_bytes(rel_url, mtime)
    if data is None:
        return False
    save_local(rel_url, data, lm)
    return True


# ---------- 模型列表解析 ----------
def fetch_model_list():
    """从首页画廊 DATA 数组解析出 (prefab, skinId) 列表。prefab 即模型目录名。"""
    html = get_text("/")
    blocks = re.findall(r"\{[^{}]*prefab[^{}]*\}", html)
    models = []
    seen = set()
    for b in blocks:
        m = re.search(r"prefab[\"']?\s*[:=]\s*[\"']([^\"']+)[\"']", b)
        if not m:
            continue
        prefab = m.group(1)
        if prefab in seen:
            continue
        seen.add(prefab)
        sk = re.search(r"skinId[\"']?\s*[:=]\s*(\d+)", b)
        models.append((prefab, sk.group(1) if sk else None))
    return models


def collect_file_refs(node, out):
    """递归收集 model3.json -> FileReferences 里所有相对文件路径字符串。"""
    if isinstance(node, str):
        if node and not node.startswith("http"):
            out.append(node)
    elif isinstance(node, list):
        for x in node:
            collect_file_refs(x, out)
    elif isinstance(node, dict):
        for v in node.values():
            collect_file_refs(v, out)


def download_model(prefab):
    model_json = "/azurlane/live2d/%s/%s.model3.json" % (prefab, prefab)
    try:
        data, lm = fetch_bytes(model_json)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return
        raise
    if data is None:
        return  # 304，本地已是最新
    save_local(model_json, data, lm)
    try:
        j = json.loads(data)
    except Exception:
        return
    refs = (j.get("FileReferences") if isinstance(j, dict) else {}) or {}
    files = []
    collect_file_refs(refs, files)
    base = "/azurlane/live2d/%s" % prefab
    for f in files:
        try:
            download(base + "/" + f.lstrip("/"))
        except Exception as e:
            print("  [warn] %s 子资源 %s: %s" % (prefab, f, e))
    download_voices(prefab, _skin_of(prefab))


def download_voices(prefab, skin_id):
    if not SYNC_VOICES or not skin_id:
        return
    gid = str(int(skin_id) // 10)
    for lang in VOICE_LANGS:
        try:
            data, lm = fetch_bytes("/data/ships/%s/%s.json" % (lang, gid))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            print("  [warn] 游戏数据 %s/%s: %s" % (lang, gid, e))
            continue
        if data is None:
            continue
        save_local("/data/ships/%s/%s.json" % (lang, gid), data, lm)
        try:
            j = json.loads(data)
        except Exception:
            continue
        skins = (((j.get("ship") or {}).get("skins")) or []) if isinstance(j, dict) else []
        for skin in skins:
            if (skin.get("prefab") or "").lower() == prefab.lower():
                for w in (skin.get("words") or []):
                    vp = w.get("voicePath")
                    if vp:
                        try:
                            download("/azurlane/%s.ogg" % vp.lstrip("/"))
                        except Exception as e:
                            print("  [warn] 语音 %s: %s" % (vp, e))


# skinId 缓存，避免重复解析
_SKIN_CACHE = {}


def _skin_of(prefab):
    return _SKIN_CACHE.get(prefab)


def download_skin_page(skin_id):
    if not skin_id:
        return
    try:
        data, lm = fetch_bytes("/cn/skins/%s/" % skin_id)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return
        print("  [warn] 皮肤页 %s: %s" % (skin_id, e))
        return
    if data is None:
        return
    save_local("/cn/skins/%s/index.html" % skin_id, data, lm)


def download_assets_from(html):
    for m in re.finditer(r'(?:src|href)=["\']((/assets/[^"\']+\.(?:js|css)))["\']', html):
        try:
            download(m.group(1))
        except Exception as e:
            print("  [warn] 资源 %s: %s" % (m.group(1), e))


def download_pages():
    try:
        html = get_text("/")
        save_local("/index.html", html.encode("utf-8"))
        download_assets_from(html)
    except Exception as e:
        print("  [warn] 画廊 /: %s" % e)
    try:
        html = get_text("/cn/")
        save_local("/cn/index.html", html.encode("utf-8"))
        download_assets_from(html)
    except Exception as e:
        print("  [warn] /cn/: %s" % e)


def main():
    os.makedirs(WEBROOT, exist_ok=True)
    print("[sync] 上游 = %s" % SOURCE_HOST)
    print("[sync] 镜像根 = %s" % WEBROOT)
    print("[sync] 语音同步 = %s (语言 %s)" % (SYNC_VOICES, ",".join(VOICE_LANGS)))

    models = fetch_model_list()
    for prefab, sk in models:
        if sk:
            _SKIN_CACHE[prefab] = sk
    if SYNC_LIMIT and SYNC_LIMIT > 0:
        models = models[:SYNC_LIMIT]
    print("[sync] 待同步模型数 = %d" % len(models))

    tasks = []
    for prefab, sk in models:
        tasks.append(("model", prefab))
        tasks.append(("thumb", prefab))
        tasks.append(("skin", sk))

    def run(task):
        kind, arg = task
        try:
            if kind == "model":
                download_model(arg)
            elif kind == "thumb":
                download("/thumbs/%s.webp" % arg)
            elif kind == "skin":
                download_skin_page(arg)
        except Exception as e:
            print("  [warn] %s %s: %s" % (kind, arg, e))

    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(run, t) for t in tasks]
        for fu in concurrent.futures.as_completed(futs):
            fu.result()
            done += 1
    print("[sync] 模型相关任务完成 = %d" % done)

    download_pages()
    print("[sync] 列表页同步完成")
    print("[sync] 全部完成")


if __name__ == "__main__":
    main()
