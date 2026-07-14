"""LLM提供商抽象层，支持Claude / OpenAI / DeepSeek / Ollama / OpenRouter / NVIDIA。"""

import json
import logging
import os
import time
from typing import Protocol

import anthropic
import httpx

logger = logging.getLogger(__name__)


class LLMProvider(Protocol):
    """LLM提供商协议。"""

    def generate(self, prompt: str, max_tokens: int = 1024) -> str:
        """生成文本响应。"""
        ...


# ── Claude ─────────────────────────────────────────────────────
class ClaudeProvider:
    """Claude API提供商。"""

    def __init__(self, model: str = "claude-sonnet-4-20250514"):
        self.model = model
        self._client = anthropic.Anthropic()

    def generate(self, prompt: str, max_tokens: int = 1024) -> str:
        try:
            message = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            return f"API调用失败: {e}"
        except anthropic.RateLimitError as e:
            logger.error(f"Claude rate limit: {e}")
            return f"API限流，请稍后重试: {e}"
        except Exception as e:
            logger.error(f"Unexpected error calling Claude: {e}")
            return f"分析失败: {e}"


# ── Ollama ─────────────────────────────────────────────────────
class OllamaProvider:
    """Ollama提供商，支持本地和云端部署。"""

    def __init__(
        self,
        model: str = "qwen2.5:14b",
        base_url: str = "http://localhost:11434",
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=300.0)

    def generate(self, prompt: str, max_tokens: int = 1024) -> str:
        for attempt in range(2):
            try:
                response = self._client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"num_predict": max_tokens},
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data.get("response", "")
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status == 502 and attempt == 0:
                    logger.warning(f"Ollama返回502，3秒后重试...")
                    time.sleep(3)
                    continue
                logger.error(f"Ollama HTTP error: {e}")
                return f"Ollama调用失败: HTTP {status}，可能是模型内存不足，请尝试更小的模型"
            except httpx.ConnectError:
                logger.error(f"Cannot connect to Ollama at {self.base_url}")
                return f"无法连接Ollama服务: {self.base_url}，请确认Ollama已启动"
            except Exception as e:
                logger.error(f"Unexpected error calling Ollama: {e}")
                return f"分析失败: {e}"
        return "Ollama调用失败"


def _resolve_api_key(api_key: str, env_var: str) -> str:
    """解析API key：配置文件优先，环境变量兜底。

    Args:
        api_key: 配置文件中填写的api_key（可为空）
        env_var: 对应的环境变量名

    Returns:
        有效的API key

    Raises:
        ValueError: 配置和环境变量都没有提供key
    """
    key = api_key or os.environ.get(env_var, "")
    if not key:
        raise ValueError(
            f"未提供API key：请在配置文件中填写 api_key，或设置环境变量 {env_var}"
        )
    return key


# ── OpenAI兼容基类（OpenAI / DeepSeek / OpenRouter / NVIDIA 共用） ──
class _OpenAICompatibleProvider:
    """OpenAI兼容API基类。"""

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        timeout: float = 120.0,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def generate(self, prompt: str, max_tokens: int = 1024) -> str:
        for attempt in range(2):
            try:
                response = self._client.post(
                    f"{self.base_url}/chat/completions",
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                    },
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in (429, 502, 503) and attempt == 0:
                    wait = 3 if status != 429 else 5
                    logger.warning(f"HTTP {status}，{wait}秒后重试...")
                    time.sleep(wait)
                    continue
                logger.error(f"API HTTP error: {e}")
                body = ""
                try:
                    body = e.response.text[:200]
                except Exception:
                    pass
                return f"API调用失败: HTTP {status} {body}"
            except httpx.ConnectError:
                logger.error(f"Cannot connect to {self.base_url}")
                return f"无法连接API: {self.base_url}"
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                return f"分析失败: {e}"
        return "API调用失败"


# ── OpenAI ─────────────────────────────────────────────────────
class OpenAIProvider(_OpenAICompatibleProvider):
    """OpenAI API提供商。

    API key: 配置文件 api_key 优先，否则读环境变量 OPENAI_API_KEY
    模型列表: https://platform.openai.com/docs/models
    base_url: 可自定义（Azure/代理/兼容端点），默认官方
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
    ):
        super().__init__(
            model=model,
            base_url=base_url,
            api_key=_resolve_api_key(api_key, "OPENAI_API_KEY"),
        )


# ── DeepSeek ───────────────────────────────────────────────────
class DeepSeekProvider(_OpenAICompatibleProvider):
    """DeepSeek API提供商（OpenAI兼容）。

    API key: 配置文件 api_key 优先，否则读环境变量 DEEPSEEK_API_KEY
    模型: deepseek-chat（V3）/ deepseek-reasoner（R1推理）
    文档: https://api-docs.deepseek.com
    """

    def __init__(
        self,
        model: str = "deepseek-chat",
        api_key: str = "",
        base_url: str = "https://api.deepseek.com/v1",
    ):
        super().__init__(
            model=model,
            base_url=base_url,
            api_key=_resolve_api_key(api_key, "DEEPSEEK_API_KEY"),
        )


# ── OpenRouter ─────────────────────────────────────────────────
class OpenRouterProvider(_OpenAICompatibleProvider):
    """OpenRouter API提供商。

    API key: 配置文件 api_key 优先，否则读环境变量 OPENROUTER_API_KEY
    模型列表: https://openrouter.ai/models
    """

    def __init__(
        self,
        model: str = "qwen/qwen-2.5-72b-instruct",
        api_key: str = "",
    ):
        super().__init__(
            model=model,
            base_url="https://openrouter.ai/api/v1",
            api_key=_resolve_api_key(api_key, "OPENROUTER_API_KEY"),
        )


# ── NVIDIA build.nvidia.com ───────────────────────────────────
class NvidiaProvider(_OpenAICompatibleProvider):
    """NVIDIA build.nvidia.com API提供商。

    API key: 配置文件 api_key 优先，否则读环境变量 NVIDIA_API_KEY
    模型列表: https://build.nvidia.com/explore/discover
    """

    def __init__(
        self,
        model: str = "nvidia/llama-3.1-nemotron-70b-instruct",
        api_key: str = "",
    ):
        super().__init__(
            model=model,
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=_resolve_api_key(api_key, "NVIDIA_API_KEY"),
        )


# ── 工厂函数 ──────────────────────────────────────────────────
def create_provider(config: dict) -> LLMProvider:
    """根据配置创建LLM提供商。

    支持的 provider: claude, openai, deepseek, ollama, openrouter, nvidia
    OpenAI兼容的提供商(openai/deepseek/openrouter/nvidia)均支持在配置文件中
    直接填写 api_key（优先），否则回退到各自的环境变量。

    Args:
        config: 完整配置字典

    Returns:
        LLMProvider实例
    """
    llm_config = config.get("llm", {})
    provider = llm_config.get("provider", "claude")

    if provider == "openai":
        oa_config = config.get("openai", {})
        return OpenAIProvider(
            model=oa_config.get("model", "gpt-4o"),
            api_key=oa_config.get("api_key", ""),
            base_url=oa_config.get("base_url", "https://api.openai.com/v1"),
        )
    elif provider == "deepseek":
        ds_config = config.get("deepseek", {})
        return DeepSeekProvider(
            model=ds_config.get("model", "deepseek-chat"),
            api_key=ds_config.get("api_key", ""),
            base_url=ds_config.get("base_url", "https://api.deepseek.com/v1"),
        )
    elif provider == "ollama":
        ollama_config = config.get("ollama", {})
        return OllamaProvider(
            model=ollama_config.get("model", "qwen2.5:14b"),
            base_url=ollama_config.get("base_url", "http://localhost:11434"),
        )
    elif provider == "openrouter":
        or_config = config.get("openrouter", {})
        return OpenRouterProvider(
            model=or_config.get("model", "qwen/qwen-2.5-72b-instruct"),
            api_key=or_config.get("api_key", ""),
        )
    elif provider == "nvidia":
        nv_config = config.get("nvidia", {})
        return NvidiaProvider(
            model=nv_config.get("model", "nvidia/llama-3.1-nemotron-70b-instruct"),
            api_key=nv_config.get("api_key", ""),
        )
    else:
        claude_config = config.get("claude", {})
        return ClaudeProvider(
            model=claude_config.get("model", "claude-sonnet-4-20250514"),
        )
