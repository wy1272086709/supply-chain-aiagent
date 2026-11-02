# FastAPI 应用镜像 — Python 3.12 + uv（依赖以 uv.lock 锁定）
# 构建时如需走官方 PyPI：--build-arg PYPI_INDEX_URL=https://pypi.org/simple
# 说明：基础镜像自带 tzdata（zoneinfo 可用）、无 curl（healthcheck 用 Python 标准库），
#       全程只依赖 PyPI 一个外部源，不碰 Debian apt 仓库。
FROM python:3.12-slim

ARG PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN pip install --no-cache-dir -i ${PYPI_INDEX_URL} uv

# 先装依赖（只有 pyproject.toml / uv.lock 变化才会击穿这层缓存）
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# 再拷业务代码（demo/ 是本地学习工程，部署不需要，且 GitHub 上本就没有）
COPY app ./app

EXPOSE 3000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-3000}"]
