"""Snowflake ID 生成器 — 移植自 npm snowflake-id（src/common/snowflake-id.ts）。

位布局（与 npm 包一致）：time(41bit) << 22 | mid(10bit) << 12 | seq(12bit)。
mid = (SNOWFLAKE_WORKER_ID or 1) % 1023，offset = SNOWFLAKE_OFFSET（默认 2024-01-01）。
所有对外 ID 均以十进制字符串序列化。
"""

from __future__ import annotations

import threading
import time


class Snowflake:
    def __init__(self, mid: int = 1, offset: int = 1704067200000) -> None:
        self.seq = 0
        self.mid = (mid or 1) % 1023
        self.offset = offset
        self.last_time = 0
        self._lock = threading.Lock()

    def generate(self) -> int:
        with self._lock:
            now = int(time.time() * 1000)
            if self.last_time == now:
                self.seq += 1
                if self.seq > 4095:
                    self.seq = 0
                    # 同毫秒序列耗尽，等待下一毫秒（npm 实现此处直接回绕，等待更安全）
                    while now == self.last_time:
                        now = int(time.time() * 1000)
            else:
                self.seq = 0
            self.last_time = now
            return ((now - self.offset) << 22) | (self.mid << 12) | self.seq


_generator: Snowflake | None = None


def init_snowflake(worker_id: int, offset: int) -> None:
    global _generator
    _generator = Snowflake(mid=worker_id, offset=offset)


def next_snowflake_id() -> str:
    if _generator is None:
        init_snowflake(1, 1704067200000)
    return str(_generator.generate())
