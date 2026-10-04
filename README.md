# Fndesk Live2D 资源站镜像

把 Fndesk（飞牛 fnOS 桌面）的 **Live2D 壁纸模型资源站**（`fndesk.roceos.net:4433`）完整镜像到本地，
对外提供**路径完全一致**的模型列表与模型下载服务，并按 env 设定的频率定时增量同步。

> 用途：现有 Fndesk Live2D 组件（星の辰提供的 `fnos-live2d`）只需把 host 指向本镜像，
> 即可获得与上游完全相同的体验，而不依赖上游站点的可用性。

---

## 工作原理

- **镜像站本质**：上游是一个 nginx 静态资源站（CORS `*`、无鉴权、纯前端跨域直拉）。
  本镜像把它的文件**原样**拉到本地，路径结构 1:1 一致。
- **本地优先 + 回源兜底**：nginx 收到请求时本地有文件直接返回；本地缺失则透明代理到上游并缓存。
  因此容器一启动就能服务，边服务边后台回填。
- **定时同步**：`entrypoint.sh` 后台按 `SYNC_INTERVAL` 周期跑 `sync.py`（首次立即执行一次），
  增量下载（本地已有文件发 `If-Modified-Since`，304 直接跳过）。

### 同步覆盖的路径（与上游 1:1）

| 内容 | 路径 | 说明 |
|---|---|---|
| 模型定义 | `/azurlane/live2d/{模型名}/{模型名}.model3.json` | 核心，含 moc3/纹理/动作/物理引用 |
| 模型子资源 | 同上目录下的 `.moc3` / `.webp` / `.physics3.json` / 动作 / 表情 | 由 model3.json 递归解析下载 |
| 缩略图 | `/thumbs/{模型名}.webp` | 画廊展示用 |
| 皮肤页 | `/cn/skins/{skinId}/` | `skinId → 模型名` 映射 |
| 列表页 | `/` 与 `/cn/` 及其 `/assets/*.js` | 模型列表 |
| 台词语音（可选） | `/data/ships/{lang}/{gid}.json` + `/azurlane/{voicePath}.ogg` | `SYNC_VOICES=true` 开启 |

---

## 快速开始（Docker）

### 方式一：从 GitHub 镜像仓库（GHCR）直接拉取

镜像由本仓库的 GitHub Actions 自动构建并推送，支持 `linux/amd64` 与 `linux/arm64`（适合 fnOS ARM 设备）：

```bash
docker run -d --name fndesk-live2d-mirror \
  -p 8080:80 \
  -v $(pwd)/data:/data/mirror \
  -e SYNC_INTERVAL=7d \
  -e SYNC_VOICES=false \
  ghcr.io/tcbomc/fndesk-live2d-mirror:latest
```

> 镜像地址：`ghcr.io/tcbomc/fndesk-live2d-mirror:latest`
> （公开仓库构建的镜像默认可公开拉取；如需私有，在仓库 *Packages* 设置里改可见性。）

### 方式二：docker-compose（推荐）

```bash
git clone https://github.com/TCBOMC/fndesk-live2d-mirror.git
cd fndesk-live2d-mirror
# 按需编辑 .env（或直接在 docker-compose.yml 的 environment 里改）
docker compose up -d --build
```

启动后访问：

```bash
curl -k https://localhost:8080/azurlane/live2d/lafei/lafei.model3.json
```

把 Fndesk Live2D 组件的 host 指向 `http://<本机IP>:8080` 即可。

### 方式三：本机构建镜像

```bash
docker build -t fndesk-live2d-mirror:latest .
docker run -d -p 8080:80 -v $(pwd)/data:/data/mirror fndesk-live2d-mirror:latest
```

---

## 环境变量

通过 `docker run -e` 或 `docker-compose.yml` 的 `environment` 配置：

| 变量 | 默认 | 说明 |
|---|---|---|
| `SOURCE_HOST` | `https://fndesk.roceos.net:4433` | 要镜像的上游地址 |
| `WEBROOT` | `/data/mirror` | 容器内镜像根目录（挂载卷持久化） |
| `SYNC_INTERVAL` | `7d` | 同步周期，支持 `7d`/`24h`/`60m`/`1w`/`3600`(秒) |
| `SYNC_WORKERS` | `6` | 并发下载线程数 |
| `SYNC_VOICES` | `false` | 是否同步台词语音（体积较大） |
| `SYNC_VOICE_LANGS` | `CN` | 语音语言：`CN,EN,JP,KR,TW` |
| `SYNC_LIMIT` | `0` | 最多同步模型数（`0`=全部）；调试可设小数 |
| `SYNC_FORCE` | `false` | `true` 时忽略本地缓存强制重下 |
| `PROXY_FALLBACK` | `true` | `false` 时本地缺失即 404（不回源） |
| `HTTPS_PROXY` | 空 | 同步/回源需走代理时填（如 `http://host.docker.internal:7897`） |

---

## 免 Docker：Windows / 直接跑 Python

核心逻辑是纯 Python 标准库，**无需 Docker 即可在任意平台运行**：

```bash
# 1) 同步模型到 ./data（需代理时设 HTTPS_PROXY=http://127.0.0.1:7897）
python3 sync.py

# 2) 起本地服务（替代 nginx，自带 CORS + 回源兜底）
python3 serve.py --webroot ./data --port 8080
```

`serve.py` 的行为与 Docker 版 nginx 一致：本地有文件直返（带 `Access-Control-Allow-Origin: *`），
本地缺失回源上游并缓存。适合测试、小规模自用；**生产/高并发仍建议用 Docker 版**。

---

## 组件对接（把 Fndesk 指向本镜像）

Fndesk Live2D 组件内部硬编码 `host` + `staticHost`。把它改为你的镜像地址即可：

```
L2D_BASE   = {你的地址}/azurlane/live2d
SU_HOST    = {你的地址}
```

即下载模型的真实地址变为：

```
{你的地址}/azurlane/live2d/{模型名}/{模型名}.model3.json
```

---

## 说明

- 上游资源为第三方游戏（碧蓝航线）Live2D 模型，本仓库仅提供**同步/镜像工具**，不内嵌任何模型文件；
  镜像内容版权归原作者所有，请遵守相关许可与当地法律使用。
- 沙箱环境无 docker，本仓库的镜像由 **GitHub Actions** 在推送时自动构建，并推送到：
  - **GHCR**：`ghcr.io/tcbomc/fndesk-live2d-mirror`（见上方「方式一」）
  - **Docker Hub**：`trseimc/fndesk-live2d-mirror`（需在本仓库 *Settings → Secrets* 配置 `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN`，缺省时仅推 GHCR）

## 镜像版本与标签

镜像标签由 `.github/workflows/docker.yml` 中的 `version-manager` 自动管理（版本号写入 `.version.json`）：

- 每次推送 `main`：build 号 `+1`（如 `v0.1.0` → `v0.1.0.1`）
- 推送 `v*` tag 或手动 `workflow_dispatch` 升级：正式版本推进、build 归零
- 每个版本同时产出 `latest`、`vX.Y.Z`、`vX.Y.Z.N` 三种标签，分别推送到 GHCR 与 Docker Hub（若已配置）
