#!/usr/bin/env bash
# 阿里云 ECS 一键部署脚本（Ubuntu / Debian / Alibaba Cloud Linux / CentOS 均可）
#
# 用法（root）：
#   bash deploy.sh                     # 自动：装 Docker → 配镜像加速 → 取代码 → 起 service
#
# 代码来源（二选一，优先级从高到低）：
#   1. 脚本同目录（或当前目录）下有 pack.sh 打的 knowledge-hub-*.tar.gz → 解压部署（国内推荐）
#   2. git clone（境外网络 / 配了代理时用）
#
# 可用环境变量覆盖：
#   APP_DIR    部署目录（默认 /opt/knowledge-hub）
#   REPO_URL   git 仓库地址
#   APP_PORT   对外端口（默认 3000）
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/knowledge-hub}"
REPO_URL="${REPO_URL:-https://github.com/wy1272086709/supply-chain-aiagent.git}"
COMPOSE_FILE="docker-compose.prod.yml"

log()  { printf '\033[32m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[deploy]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[deploy]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "请用 root 运行：sudo bash deploy.sh"

# ---------- 1. 公网 IP（阿里云元数据优先，失败再走外网探测） ----------
get_public_ip() {
  curl -fs --connect-timeout 2 http://100.100.100.200/latest/meta-data/eipv4 2>/dev/null \
    || curl -fs --connect-timeout 2 http://100.100.100.200/latest/meta-data/public-ipv4 2>/dev/null \
    || curl -fs --connect-timeout 5 https://ifconfig.me 2>/dev/null \
    || true
}

# ---------- 2. 安装 Docker + Compose 插件 ----------
install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log "Docker 已安装：$(docker --version)"
    return
  fi
  log "安装 Docker（get.docker.com --mirror Aliyun）……"
  if curl -fsSL https://get.docker.com | sh -s -- --mirror Aliyun; then
    :
  else
    warn "官方脚本失败，改用系统源安装"
    if command -v apt-get >/dev/null; then
      apt-get update && apt-get install -y docker.io docker-compose-v2
    elif command -v yum >/dev/null; then
      yum install -y docker docker-compose-plugin
    else
      die "无法识别包管理器，请手动安装 Docker"
    fi
  fi
  systemctl enable --now docker
  docker compose version >/dev/null 2>&1 || die "docker compose 插件不可用，请检查安装"
}

# ---------- 3. Docker Hub 镜像加速（国内拉镜像必需） ----------
setup_mirror() {
  mkdir -p /etc/docker
  if [ -f /etc/docker/daemon.json ]; then
    warn "/etc/docker/daemon.json 已存在，跳过加速配置；如后续拉镜像超时，请自行在其中的 registry-mirrors 换地址"
    return
  fi
  # 公共加速地址时效性不定；若拉取超时，可替换为阿里云容器镜像服务控制台提供的个人加速地址
  cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://docker.m.daocloud.io"
  ]
}
EOF
  systemctl daemon-reload && systemctl restart docker
  log "已配置 Docker 镜像加速（/etc/docker/daemon.json）"
}

# ---------- 4. 小内存机器加 swap（ES + Neo4j 至少吃 4G，2-4G 内存建议开启） ----------
setup_swap() {
  if swapon --show 2>/dev/null | grep -q .; then return; fi
  if [ -f /swapfile ]; then return; fi
  local total_mb
  total_mb=$(free -m | awk '/^Mem:/{print $2}')
  if [ "$total_mb" -lt 6144 ]; then
    log "内存 ${total_mb}M < 6G，创建 2G swap 兜底（不用可 swapoff -a 并删 /swapfile）"
    dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none \
      && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
}

# ---------- 5. 取代码 ----------
fetch_code() {
  local tarball=""
  local d
  for d in "$(cd "$(dirname "$0")" && pwd)" "$(pwd)"; do
    t=$(ls "$d"/knowledge-hub-*.tar.gz 2>/dev/null | head -n1)
    [ -n "$t" ] && tarball="$t" && break
  done

  if [ -n "$tarball" ]; then
    log "从压缩包部署：$tarball → $APP_DIR（.env 和 volumes/ 数据会保留）"
    mkdir -p "$APP_DIR"
    tar -xzf "$tarball" -C "$APP_DIR" --strip-components=1
  elif [ -f "$APP_DIR/$COMPOSE_FILE" ]; then
    log "代码已存在于 $APP_DIR，沿用（更新请重新放压缩包运行本脚本）"
  else
    log "git clone（国内服务器如超时：本地跑 deploy/pack.sh 打包后上传）"
    command -v git >/dev/null || die "未安装 git 且没有找到压缩包"
    mkdir -p "$(dirname "$APP_DIR")"
    git clone --depth 1 "$REPO_URL" "$APP_DIR" || die "克隆失败（可本地 pack.sh 打包上传）"
  fi
}

# ---------- 6. 初始化 .env（仅首次生成；密钥自动填） ----------
setup_env() {
  cd "$APP_DIR"
  if [ -f .env ]; then
    log ".env 已存在，沿用"
    return
  fi
  cp .env.example .env

  local ip
  ip=$(get_public_ip)
  if [ -n "$ip" ]; then
    sed -i "s|^APP_PUBLIC_URL=.*|APP_PUBLIC_URL=http://${ip}:${APP_PORT:-3000}|" .env
    sed -i "s|^RUSTFS_PUBLIC_URL=.*|RUSTFS_PUBLIC_URL=http://${ip}:9000|" .env
  fi
  # JWT 密钥换成随机值（仅首次生成时替换，重启不会导致已发 token 失效）
  if command -v openssl >/dev/null; then
    sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$(openssl rand -hex 32)|" .env
  fi
  warn "已生成 $APP_DIR/.env —— 请填入 API Key（不填则 RAG/KG/对话降级）："
  warn "  OPENAI_API_KEY / EMBEDDING_API_KEY / DASHSCOPE_API_KEY 等，编辑命令：vim $APP_DIR/.env"
}

# ---------- 7. 构建并启动 ----------
main() {
  install_docker
  setup_mirror
  setup_swap
  fetch_code
  setup_env

  log "构建镜像（首次约 5-15 分钟：Python 依赖 + ES 装 IK 插件）……"
  docker compose -f "$COMPOSE_FILE" build

  log "启动全部服务……"
  docker compose -f "$COMPOSE_FILE" up -d

  log "等待应用就绪（首次要等中间件健康检查通过，约 1-4 分钟）……"
  local ok=""
  for _ in $(seq 1 60); do
    if curl -fs "http://127.0.0.1:${APP_PORT:-3000}/" >/dev/null 2>&1; then ok=1; break; fi
    sleep 5
  done

  echo
  docker compose -f "$COMPOSE_FILE" ps
  echo
  if [ -n "$ok" ]; then
    local ip
    ip=$(get_public_ip); ip="${ip:-<服务器公网IP>}"
    log "部署成功 ✅"
    cat <<EOF

  应用地址:    http://${ip}:${APP_PORT:-3000}
  接口文档:    http://${ip}:${APP_PORT:-3000}/docs
  种子账号:    admin / reviewer / user，密码均为 123456（上线后请改密）

后续操作:
  改了 .env 后生效:   cd $APP_DIR && docker compose -f $COMPOSE_FILE up -d app
  看日志:             docker compose -f $COMPOSE_FILE logs -f app
  重启 / 停止:         docker compose -f $COMPOSE_FILE restart / down
  更新代码重新部署:    本地 pack.sh → scp 上传 tar.gz → 重跑 deploy.sh

安全组（阿里云控制台 → ECS → 安全组 → 入方向）需放行:
  ${APP_PORT:-3000}/tcp  应用端口
  9000/tcp              RustFS 文件直链
EOF
  else
    warn "应用 5 分钟内未就绪，排查："
    warn "  cd $APP_DIR && docker compose -f $COMPOSE_FILE ps"
    warn "  docker compose -f $COMPOSE_FILE logs app --tail 100"
    exit 1
  fi
}

main "$@"
