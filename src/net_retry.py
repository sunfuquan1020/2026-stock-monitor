"""网络调用重试工具。

东财 (eastmoney) push2/datacenter 接口对部分大陆住宅 IP 有连接级风控,
表现为间歇性 SSL UNEXPECTED_EOF / 连接被拒——同一代码换时段/网络即恢复。
绝大多数间歇性失败靠"指数退避 + 抖动"重试即可吃掉, 无需切换数据源。
"""

import logging
import random
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 1.0  # 首次重试前等待(秒)
DEFAULT_BACKOFF = 2.0     # 每次退避倍数
DEFAULT_JITTER = 0.5      # 随机抖动上限(秒), 避免同步重试


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    backoff: float = DEFAULT_BACKOFF,
    jitter: float = DEFAULT_JITTER,
    label: str = "call",
) -> T:
    """执行 fn(), 失败时指数退避重试, 全部失败后抛最后一次异常。

    Args:
        fn: 无参可调用, 返回结果
        attempts: 总尝试次数(含首次)
        base_delay: 首次重试前基础等待
        backoff: 退避倍数 (第 i 次重试等待 base_delay * backoff**i)
        jitter: 每次额外随机等待 [0, jitter)
        label: 日志标识

    Returns:
        fn() 的返回值

    Raises:
        最后一次尝试的异常
    """
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - 上层按需转成 warning
            last_exc = e
            if i < attempts - 1:
                delay = base_delay * (backoff**i) + random.uniform(0, jitter)
                logger.warning(
                    f"{label} 第{i + 1}/{attempts}次失败: {str(e)[:80]}; "
                    f"{delay:.1f}s 后重试"
                )
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc
