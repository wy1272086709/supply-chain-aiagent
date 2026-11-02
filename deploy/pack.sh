#!/usr/bin/env bash
# 本地打包脚本（在 Mac / 开发机上运行）：把当前工作区打成部署包（不要求先 commit）
#
# 产物：dist/knowledge-hub-<时间戳>.tar.gz
# 排除：.git / .venv / volumes（数据）/ .env（密钥）/ demo（本地学习工程，不上服务器）
#       / __pycache__ / *.pyc / *.log / dist / .pytest_cache / .DS_Store / test.py
# 用法：bash deploy/pack.sh
set -euo pipefail
cd "$(dirname "$0")/.."

out="dist/knowledge-hub-$(date +%Y%m%d-%H%M).tar.gz"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

mkdir -p "$stage/knowledge-hub" dist
rsync -a \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='.pytest_cache' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  --exclude='.env' \
  --exclude='volumes' \
  --exclude='demo' \
  --exclude='dist' \
  --exclude='*.log' \
  --exclude='test.py' \
  ./ "$stage/knowledge-hub/"

tar -czf "$out" -C "$stage" knowledge-hub

echo "✅ 已打包：$out ($(du -h "$out" | cut -f1))"
echo
echo "下一步（把包和部署脚本传到阿里云服务器，IP 换成你的）："
echo "  scp $out deploy/deploy.sh root@<服务器IP>:/root/"
echo "  ssh root@<服务器IP>"
echo "  bash /root/deploy.sh"
