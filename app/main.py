"""应用入口 — 对应 src/main.ts + app.module.ts。

- 端口 PORT（默认 3000），无全局前缀
- CORS：反射任意 Origin + credentials
- 全局异常处理器输出 Nest 风格 {statusCode, message, error}
- lifespan：启动连 ES/Neo4j/RustFS/RabbitMQ，关闭时清理
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.errors import (
    AppError,
    app_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.snowflake import init_snowflake
from app.modules.ai.router import router as ai_router
from app.modules.auth.router import router as auth_router
from app.modules.documents.router import router as documents_router
from app.modules.graph.router import router as graph_router
from app.modules.mq import consumer as mq_consumer
from app.modules.mq import rabbitmq
from app.modules.pipeline import es_client, graph_build, search_index, vector_index
from app.modules.search.router import router as search_router
from app.modules.storage import rustfs
from app.modules.teams.router import router as teams_router
from app.modules.users.permission_router import router as permissions_router
from app.modules.users.role_router import router as roles_router
from app.modules.users.user_router import router as users_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    cfg = get_settings()
    init_snowflake(cfg.snowflake_worker_id, cfg.snowflake_offset)

    # ES：连接 + 确保两个索引 mapping；不可用降级为跳过写入
    if await es_client.connect_es() is not None:
        await search_index.ensure_index()
        await vector_index.ensure_index()

    # Neo4j：不可用降级
    await graph_build.connect()

    # RustFS 桶确保（后台执行，失败降级）
    asyncio.create_task(rustfs.ensure_bucket())

    # RabbitMQ：注册消费者后连接；启用且连不上时按 TS 语义硬失败
    mq_consumer.register_all()
    await rabbitmq.connect()

    logger.info("knowledge-hub backend 已启动（FastAPI，端口 %d）", cfg.port)
    yield

    await rabbitmq.close()
    await es_client.close_es()
    await graph_build.close()


def create_app() -> FastAPI:
    app = FastAPI(title="knowledge-hub-backend", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.get("/")
    async def root():
        return "Hello World!"

    app.include_router(auth_router)
    app.include_router(users_router)
    app.include_router(roles_router)
    app.include_router(permissions_router)
    app.include_router(teams_router)
    app.include_router(documents_router)
    app.include_router(search_router)
    app.include_router(graph_router)
    app.include_router(ai_router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=get_settings().port, reload=False)


if __name__ == "__main__":
    main()
