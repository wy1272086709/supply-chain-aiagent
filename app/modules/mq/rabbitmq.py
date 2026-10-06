"""RabbitMQ 连接/拓扑/发布/消费 — 对应 rabbitmq.service.ts（aio-pika robust）。

- persistent JSON 消息，发布失败只记日志返回 False
- 消费成功 ack；失败 nack(requeue=False)（无 DLQ，消息丢弃——与 TS 一致）
- 启动连接超时（默认 15s）直接抛错，阻断应用启动
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

import aio_pika

from app.core.config import get_settings
from app.modules.mq import constants as mq

logger = logging.getLogger(__name__)

_connection: aio_pika.abc.AbstractRobustConnection | None = None
_channel: aio_pika.abc.AbstractRobustChannel | None = None
_handlers: dict[str, Callable[[dict, bytes], Awaitable[None]]] = {}
_consumers_bound = False


def register_handler(queue: str, handler: Callable[[dict, bytes], Awaitable[None]]) -> None:
    _handlers[queue] = handler


async def connect() -> None:
    """连接 + 声明拓扑 + 绑定消费者。失败抛错（启动期硬失败，与 TS 一致）。"""
    global _connection, _channel, _consumers_bound
    cfg = get_settings()
    if not cfg.rabbitmq_enabled:
        logger.warning("RabbitMQ 已禁用（RABBITMQ_ENABLED=false），管线消息不投递")
        return

    try:
        _connection = await asyncio.wait_for(
            aio_pika.connect_robust(cfg.rabbitmq_url), timeout=cfg.rabbitmq_connect_timeout_ms / 1000
        )
    except Exception as e:  # noqa: BLE001
        logger.error("RabbitMQ 连接失败：%s", e)
        raise

    _channel = await _connection.channel()
    await _declare_topology()
    await _bind_consumers()
    _consumers_bound = True
    logger.info("RabbitMQ 已连接并完成拓扑声明：%s", cfg.rabbitmq_url)


async def _declare_topology() -> None:
    assert _channel is not None
    for queue, (exchange, routing_keys) in mq.BINDINGS.items():
        ex = await _channel.declare_exchange(exchange, aio_pika.ExchangeType.TOPIC, durable=True)
        q = await _channel.declare_queue(queue, durable=True)
        for rk in routing_keys:
            await q.bind(ex, routing_key=rk)


async def _bind_consumers() -> None:
    assert _channel is not None
    for queue, handler in _handlers.items():
        q = await _channel.declare_queue(queue, durable=True)

        async def _on_message(message: aio_pika.abc.AbstractIncomingMessage, _handler=handler) -> None:
            body_raw = message.body
            try:
                body = json.loads(body_raw.decode("utf-8"))
                await _handler(body, body_raw)
                await message.ack()
            except Exception as e:  # noqa: BLE001
                logger.error("MQ 消费失败（丢弃，不重回）：queue=%s, %s", queue, e)
                try:
                    await message.nack(requeue=False)
                except Exception:  # noqa: BLE001
                    pass

        await q.consume(_on_message)


async def publish(exchange: str, routing_key: str, payload: dict) -> bool:
    """persistent JSON；失败只记日志返回 False。"""
    if _channel is None:
        logger.warning("跳过 MQ 发布（RabbitMQ 未连接）：exchange=%s, rk=%s", exchange, routing_key)
        return False
    try:
        ex = await _channel.declare_exchange(exchange, aio_pika.ExchangeType.TOPIC, durable=True)
        message = aio_pika.Message(
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        await ex.publish(message, routing_key=routing_key)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("MQ 发布失败：exchange=%s, rk=%s, %s", exchange, routing_key, e)
        return False


async def close() -> None:
    global _connection, _channel, _consumers_bound
    if _connection is not None:
        try:
            await _connection.close()
        except Exception:  # noqa: BLE001
            pass
        _connection = None
        _channel = None
        _consumers_bound = False
