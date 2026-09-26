"""HTTP 传输层 + 代理环境修正。

## 东财"接口挂了"的真实根因(2026-08-03 定位)

不是接口失效, 也不是 requests/httpx/urllib 的差别 —— 是 **`no_proxy` 让东财走了直连**。

用户 `~/.zshrc` 里有:

    no_proxy=localhost,127.0.0.1,eastmoney.com,push2his.eastmoney.com

`run.sh` 会 `source ~/.zshrc`, 于是管线里所有东财请求绕过 Clash 直连,
而本机没有境内出口, 直连东财在 TLS 层就被阻断。同一 URL 对照实测:

| 环境 | urllib | requests | requests+强制代理 |
|------|--------|----------|------------------|
| 东财走代理 | ✅ | ✅ | ✅ |
| 东财走直连(no_proxy) | ❌ SSL EOF | ❌ SSL EOF | ✅ |

即: 两种客户端表现完全一致, **决定成败的是走不走代理**。

`ensure_domestic_via_proxy()` 在进程启动时把这些域名从 no_proxy 里摘掉,
一次性修好 akshare(requests) / httpx / urllib 所有通道, 使项目不依赖用户 shell 配置。

⚠️ 例外: `push2.eastmoney.com`(实时资金流) 即使走代理也只有偶发成功,
那是大响应被隧道截断, 必须换源。见 fallback_sources.sina_fund_flow。
"""

import gzip
import json
import logging
import os
import ssl
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# 这些境内数据域名必须走代理: 本机无境内出口, 直连会被 TLS 层阻断。
# 若将来本机有了直连境内的能力, 设环境变量 STOCK_ALLOW_DIRECT_CN=1 关闭此修正。
DOMESTIC_PROXY_HOSTS = (
    "eastmoney.com",
    "gtimg.cn",
    "hexin.cn",
    "sina.com.cn",
    "szse.cn",
    "sse.com.cn",
    "10jqka.com.cn",
    "cninfo.com.cn",
)


def ensure_domestic_via_proxy() -> list[str]:
    """把境内数据域名从 NO_PROXY/no_proxy 中摘除, 强制它们走代理。

    必须在任何网络调用之前执行(main 入口第一件事)。requests / httpx / urllib
    都在建连时读这两个环境变量, 改掉即对三者同时生效。

    Returns:
        被摘掉的条目列表(供日志/报告说明), 未改动时为空
    """
    if os.environ.get("STOCK_ALLOW_DIRECT_CN") == "1":
        return []

    removed: list[str] = []
    for var in ("no_proxy", "NO_PROXY"):
        raw = os.environ.get(var)
        if not raw:
            continue
        kept = []
        for item in raw.split(","):
            entry = item.strip()
            if not entry:
                continue
            bare = entry.lstrip(".")
            if any(
                bare == h or bare.endswith("." + h) for h in DOMESTIC_PROXY_HOSTS
            ):
                removed.append(entry)
            else:
                kept.append(entry)
        if removed:
            os.environ[var] = ",".join(kept)

    if removed:
        logger.info(
            f"已从 no_proxy 摘除境内数据域名(强制走代理): {', '.join(sorted(set(removed)))}"
        )
    return sorted(set(removed))

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 25.0

# 部分交易所站点证书链在本机不完整, 且返回的是公开行情数据, 不做校验
_LAX_CTX = ssl.create_default_context()
_LAX_CTX.check_hostname = False
_LAX_CTX.verify_mode = ssl.CERT_NONE


def urllib_get_bytes(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    *,
    lax_ssl: bool = False,
) -> bytes:
    """GET 原始字节。失败直接抛异常, 由调用方决定降级。"""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(headers or {})}
    )
    ctx = _LAX_CTX if lax_ssl else None
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw


def urllib_get_text(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    *,
    encoding: str = "utf-8",
    lax_ssl: bool = False,
) -> str:
    """GET 文本, 按指定编码解码(容错)。"""
    raw = urllib_get_bytes(url, params, headers, timeout, lax_ssl=lax_ssl)
    return raw.decode(encoding, "ignore")


def urllib_get_json(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    *,
    lax_ssl: bool = False,
) -> dict | list:
    """GET 并解析 JSON。"""
    text = urllib_get_text(
        url, params, headers, timeout, encoding="utf-8", lax_ssl=lax_ssl
    )
    return json.loads(text)


def urllib_get_jsonp(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    *,
    encoding: str = "utf-8",
    lax_ssl: bool = False,
    bracket: str = "[",
) -> dict | list:
    """GET 并剥 JSONP 外壳。

    Args:
        bracket: 载荷起始括号, "[" 取数组 / "{" 取对象
    """
    text = urllib_get_text(
        url, params, headers, timeout, encoding=encoding, lax_ssl=lax_ssl
    )
    close = "]" if bracket == "[" else "}"
    start = text.find(bracket)
    end = text.rfind(close)
    if start < 0 or end <= start:
        raise ValueError(f"JSONP 载荷未找到 ({bracket}...{close}): {text[:80]}")
    return json.loads(text[start : end + 1])
