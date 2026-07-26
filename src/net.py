"""国内数据源网络兼容层: 强制 IPv4 解析。

背景: 东财等国内站点同时发布 A/AAAA 记录, 但部分网络环境到其 IPv6 的
路由是坏的 —— TCP 能连上, TLS ClientHello 之后连接被重置, Python 侧表现为
``SSLError(SSLEOFError(8, '[SSL: UNEXPECTED_EOF_WHILE_READING]'))``。
urllib3 默认优先 AAAA, 于是每次都走到坏的 IPv6 路径, 重试无用 (确定性失败)。

修复: 对已知的国内域名, 在 ``socket.getaddrinfo`` 层面只返回 IPv4 结果。
不影响美股/港股数据源 (Finnhub/Yahoo), 它们继续走双栈。

用法 (在程序入口调用一次):
    from src.net import force_ipv4_for_domestic_hosts
    force_ipv4_for_domestic_hosts()
"""

import logging
import socket

logger = logging.getLogger(__name__)

# 需要强制 IPv4 的国内域名后缀 (东财/新浪/腾讯/同花顺)
DOMESTIC_HOST_SUFFIXES = (
    "eastmoney.com",
    "sina.com.cn",
    "sinajs.cn",
    "gtimg.cn",
    "qq.com",
    "10jqka.com.cn",
)

_installed = False


def _is_domestic(host: str) -> bool:
    """判断主机名是否属于需要强制 IPv4 的国内域名。"""
    if not isinstance(host, str):
        return False
    h = host.lower().rstrip(".")
    return any(h == s or h.endswith("." + s) for s in DOMESTIC_HOST_SUFFIXES)


def force_ipv4_for_domestic_hosts() -> None:
    """全局安装 getaddrinfo 补丁: 国内域名只解析 IPv4。

    幂等 —— 重复调用只安装一次。IPv4 解析失败时回退到原始双栈解析,
    避免在纯 IPv6 环境下把能用的连接也打断。
    """
    global _installed
    if _installed:
        return

    original = socket.getaddrinfo

    def getaddrinfo_ipv4_domestic(host, port, family=0, type=0, proto=0, flags=0):
        if family == 0 and _is_domestic(host):
            try:
                return original(host, port, socket.AF_INET, type, proto, flags)
            except socket.gaierror:
                logger.debug(f"{host} 无 IPv4 记录, 回退双栈解析")
        return original(host, port, family, type, proto, flags)

    socket.getaddrinfo = getaddrinfo_ipv4_domestic
    _installed = True
    logger.debug("已启用国内数据源 IPv4 强制解析")
