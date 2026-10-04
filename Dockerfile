# Fndesk Live2D 资源站镜像
# 基于 nginx:alpine，内置 Python 同步脚本，对外提供与上游完全相同的
# 模型列表 + 模型下载服务，并按 env 设定的频率定时增量同步。
FROM nginx:alpine

# python3 用于运行同步脚本；ca-certificates 用于校验上游 TLS
RUN apk add --no-cache python3 ca-certificates

WORKDIR /app

COPY sync.py /app/sync.py
COPY entrypoint.sh /app/entrypoint.sh
COPY nginx.conf.template /app/nginx.conf.template
RUN chmod +x /app/entrypoint.sh

# ---- 默认配置（可用 docker run -e 覆盖）----
ENV SOURCE_HOST=https://fndesk.roceos.net:4433 \
    WEBROOT=/data/mirror \
    SYNC_INTERVAL=7d \
    SYNC_WORKERS=6 \
    SYNC_VOICES=false \
    SYNC_VOICE_LANGS=CN \
    SYNC_LIMIT=0 \
    SYNC_FORCE=false \
    PROXY_FALLBACK=true

# 镜像数据存储卷（持久化，避免容器重建后重新全量拉取）
VOLUME ["/data/mirror"]

EXPOSE 80

ENTRYPOINT ["/app/entrypoint.sh"]
