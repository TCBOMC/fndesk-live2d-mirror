#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fndesk Live2D 资源站 —— 纯 Python 本地服务（替代 nginx，无需 Docker）。

功能：
  1. 本地 webroot 已有的文件：直接以静态文件返回，并带 CORS 头。
  2. 本地缺失的文件：回源到上游 SOURCE_HOST（默认 https://fndesk.roceos.net:4433），
     返回内容并顺手缓存到本地 webroot，下次直接本地命中。
  3. 全程 Access-Control-Allow-Origin: *，与上游行为一致，组件可纯浏览器跨域直拉。

用法：
  python3 serve.py --webroot ./data --port 8080
  # 自定义上游（默认读环境变量 SOURCE_HOST，否则用官方地址）
  SOURCE_HOST=https://my-mirror.example.com python3 serve.py --webroot ./data
  # 需要代理才能访问上游时，自动读取 HTTP_PROXY / HTTPS_PROXY 环境变量
  HTTPS_PROXY=http://127.0.0.1:7897 python3 serve.py --webroot ./data
"""
import argparse
import os
import ssl
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("SOURCE_HOST", "https://fndesk.roceos.net:4433").rstrip("/")
WEBROOT = "."

# 访问上游时关闭证书校验（上游使用 4433 自签证书）
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def make_opener():
    """构造 opener；自动尊重 HTTP_PROXY / HTTPS_PROXY 环境变量。"""
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


_OPENER = make_opener()


class L2DHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEBROOT, **kwargs)

    # 不往 stderr 刷每请求日志也可，这里保留简洁日志便于测试
    def log_message(self, fmt, *args):
        sys_print = super().log_message
        sys_print(fmt, *args)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")

    def end_headers(self):
        self._cors()
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        rel = self.path.lstrip("/")
        local = os.path.normpath(os.path.join(WEBROOT, rel))
        if os.path.isfile(local):
            return super().do_GET()
        self._proxy_upstream(rel, local)

    def do_HEAD(self):
        rel = self.path.lstrip("/")
        local = os.path.normpath(os.path.join(WEBROOT, rel))
        if os.path.isfile(local):
            return super().do_HEAD()
        self._proxy_upstream(rel, local, head=True)

    def _proxy_upstream(self, rel, local, head=False):
        url = UPSTREAM + "/" + rel
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            resp = _OPENER.open(req, timeout=30)
            data = b"" if head else resp.read()
            self.send_response(resp.status)
            ct = resp.headers.get("Content-Type", "application/octet-stream")
            self.send_header("Content-Type", ct)
            if not head:
                self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            if not head:
                self.wfile.write(data)
                # 缓存到本地 webroot，下次直接命中
                try:
                    os.makedirs(os.path.dirname(local), exist_ok=True)
                    with open(local, "wb") as f:
                        f.write(data)
                except OSError:
                    pass
        except urllib.error.HTTPError as e:
            self.send_error(e.code, e.reason)
        except Exception as e:  # noqa: BLE001
            self.send_error(502, "upstream error: %s" % e)


def main():
    global WEBROOT
    ap = argparse.ArgumentParser(description="Fndesk Live2D 纯 Python 本地镜像服务")
    ap.add_argument("--webroot", default=os.environ.get("WEBROOT", "./data"),
                    help="已同步资源的本地目录（默认 ./data，也可用 WEBROOT 环境变量）")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")),
                    help="监听端口（默认 8080）")
    ap.add_argument("--bind", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    args = ap.parse_args()
    WEBROOT = os.path.abspath(args.webroot)
    os.makedirs(WEBROOT, exist_ok=True)
    print("Live2D mirror serving:")
    print("  webroot  : %s" % WEBROOT)
    print("  upstream : %s" % UPSTREAM)
    print("  listen   : http://%s:%d/" % (args.bind, args.port))
    httpd = ThreadingHTTPServer((args.bind, args.port), L2DHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
