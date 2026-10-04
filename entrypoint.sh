#!/bin/sh
# Fndesk Live2D 镜像站入口：
#   1. 根据环境变量把 nginx 模板渲染成实际配置（上游地址 / 回源开关）
#   2. 后台按 SYNC_INTERVAL 周期运行同步（首次立即跑一次，边服务边回填）
#   3. 前台启动 nginx 提供镜像服务
set -eu

# ---- 读取/补全环境变量 ----
: "${SOURCE_HOST:=https://fndesk.roceos.net:4433}"
: "${WEBROOT:=/data/mirror}"
: "${SYNC_INTERVAL:=7d}"
: "${PROXY_FALLBACK:=true}"
: "${SYNC_WORKERS:=6}"
: "${SYNC_VOICES:=false}"
: "${SYNC_VOICE_LANGS:=CN}"
: "${SYNC_LIMIT:=0}"
: "${SYNC_FORCE:=false}"

mkdir -p "$WEBROOT"

# 上游 Host（用于 proxy_set_header）
UP_HOST=$(echo "$SOURCE_HOST" | sed -E 's#^https?://##; s#/+$##')

# 把 "7d/24h/60m/1w/3600" 解析成秒；非法或纯数字按秒，空值回退 7 天
parse_interval() {
  v="$1"
  case "$v" in
    *w) echo $(( ${v%w} * 604800 )) ;;
    *d) echo $(( ${v%d} * 86400 )) ;;
    *h) echo $(( ${v%h} * 3600 )) ;;
    *m) echo $(( ${v%m} * 60 )) ;;
    *)  case "$v" in
          ''|*[!0-9]*) echo 604800 ;;
          *) echo "$v" ;;
        esac ;;
  esac
}
INTERVAL_S=$(parse_interval "$SYNC_INTERVAL")
echo "[entrypoint] 同步周期 = ${INTERVAL_S}s (来自 '${SYNC_INTERVAL}')"

# 回源兜底：true 时本地缺失的请求代理到上游（透明镜像）；false 时缺失即 404
if [ "$PROXY_FALLBACK" = "true" ]; then
  FALLBACK="@upstream"
else
  FALLBACK="=404"
fi

# 渲染 nginx 配置
sed -e "s#___UPSTREAM___#${SOURCE_HOST}#g" \
    -e "s#___UPSTREAM_HOST___#${UP_HOST}#g" \
    -e "s#___FALLBACK___#${FALLBACK}#g" \
    /app/nginx.conf.template > /etc/nginx/http.d/default.conf

# 透传给同步脚本
export SOURCE_HOST WEBROOT SYNC_WORKERS SYNC_VOICES SYNC_VOICE_LANGS SYNC_LIMIT SYNC_FORCE

echo "[entrypoint] 启动后台周期同步（首次立即执行）"
( while true; do
    echo "[sync $(date -u +%FT%TZ)] 开始"
    if python3 /app/sync.py; then
      echo "[sync $(date -u +%FT%TZ)] 完成"
    else
      echo "[sync $(date -u +%FT%TZ)] 失败（下个周期重试）"
    fi
    sleep "$INTERVAL_S"
  done ) &

echo "[entrypoint] 启动 nginx"
exec nginx -g 'daemon off;'
