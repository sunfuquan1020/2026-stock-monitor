"""Tests for LLM provider abstraction."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.llm import (
    ClaudeProvider,
    DeepSeekProvider,
    OllamaProvider,
    OpenAIProvider,
    create_provider,
)


class TestCreateProvider:
    def test_default_creates_claude(self):
        config = {}
        provider = create_provider(config)
        assert isinstance(provider, ClaudeProvider)

    def test_claude_provider(self):
        config = {
            "llm": {"provider": "claude"},
            "claude": {"model": "claude-opus-4-20250514"},
        }
        provider = create_provider(config)
        assert isinstance(provider, ClaudeProvider)
        assert provider.model == "claude-opus-4-20250514"

    def test_ollama_provider(self):
        config = {
            "llm": {"provider": "ollama"},
            "ollama": {
                "model": "qwen2.5:14b",
                "base_url": "http://localhost:11434",
            },
        }
        provider = create_provider(config)
        assert isinstance(provider, OllamaProvider)
        assert provider.model == "qwen2.5:14b"
        assert provider.base_url == "http://localhost:11434"

    def test_ollama_remote_url(self):
        config = {
            "llm": {"provider": "ollama"},
            "ollama": {
                "model": "llama3:70b",
                "base_url": "http://gpu-server:11434",
            },
        }
        provider = create_provider(config)
        assert isinstance(provider, OllamaProvider)
        assert provider.base_url == "http://gpu-server:11434"

    def test_ollama_strips_trailing_slash(self):
        config = {
            "llm": {"provider": "ollama"},
            "ollama": {
                "model": "qwen2.5:14b",
                "base_url": "http://localhost:11434/",
            },
        }
        provider = create_provider(config)
        assert provider.base_url == "http://localhost:11434"

    def test_openai_provider_key_from_config(self):
        config = {
            "llm": {"provider": "openai"},
            "openai": {"model": "gpt-4o-mini", "api_key": "sk-config-key"},
        }
        provider = create_provider(config)
        assert isinstance(provider, OpenAIProvider)
        assert provider.model == "gpt-4o-mini"
        assert provider.base_url == "https://api.openai.com/v1"

    def test_openai_custom_base_url(self):
        config = {
            "llm": {"provider": "openai"},
            "openai": {
                "model": "gpt-4o",
                "api_key": "sk-x",
                "base_url": "https://proxy.example.com/v1/",
            },
        }
        provider = create_provider(config)
        assert provider.base_url == "https://proxy.example.com/v1"

    def test_openai_key_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-key")
        config = {"llm": {"provider": "openai"}, "openai": {"model": "gpt-4o"}}
        provider = create_provider(config)
        assert isinstance(provider, OpenAIProvider)

    def test_openai_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        config = {"llm": {"provider": "openai"}, "openai": {"model": "gpt-4o"}}
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            create_provider(config)

    def test_deepseek_provider_key_from_config(self):
        config = {
            "llm": {"provider": "deepseek"},
            "deepseek": {"model": "deepseek-reasoner", "api_key": "sk-ds"},
        }
        provider = create_provider(config)
        assert isinstance(provider, DeepSeekProvider)
        assert provider.model == "deepseek-reasoner"
        assert provider.base_url == "https://api.deepseek.com/v1"

    def test_deepseek_key_from_env(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds-env")
        config = {"llm": {"provider": "deepseek"}, "deepseek": {}}
        provider = create_provider(config)
        assert isinstance(provider, DeepSeekProvider)
        assert provider.model == "deepseek-chat"

    def test_deepseek_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        config = {"llm": {"provider": "deepseek"}, "deepseek": {}}
        with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            create_provider(config)


class TestOllamaProvider:
    @patch("src.llm.httpx.Client")
    def test_generate_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "分析结果"}
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        provider = OllamaProvider(model="qwen2.5:14b")
        result = provider.generate("测试提示词")

        assert result == "分析结果"
        mock_client.post.assert_called_once_with(
            "http://localhost:11434/api/generate",
            json={
                "model": "qwen2.5:14b",
                "prompt": "测试提示词",
                "stream": False,
                "options": {"num_predict": 1024},
            },
        )

    @patch("src.llm.httpx.Client")
    def test_generate_http_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "Server Error",
            request=MagicMock(),
            response=mock_response,
        )
        mock_client_cls.return_value = mock_client

        provider = OllamaProvider()
        result = provider.generate("test")

        assert "Ollama调用失败" in result

    @patch("src.llm.httpx.Client")
    def test_generate_connection_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client_cls.return_value = mock_client

        provider = OllamaProvider()
        result = provider.generate("test")

        assert "无法连接Ollama服务" in result


class TestOpenAICompatibleProvider:
    @patch("src.llm.httpx.Client")
    def test_generate_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "分析结果"}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        provider = OpenAIProvider(model="gpt-4o", api_key="sk-x")
        result = provider.generate("测试提示词")

        assert result == "分析结果"
        mock_client.post.assert_called_once_with(
            "https://api.openai.com/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "测试提示词"}],
                "max_tokens": 1024,
            },
        )

    @patch("src.llm.httpx.Client")
    def test_generate_http_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "invalid api key"
        mock_client.post.side_effect = httpx.HTTPStatusError(
            "Unauthorized",
            request=MagicMock(),
            response=mock_response,
        )
        mock_client_cls.return_value = mock_client

        provider = DeepSeekProvider(api_key="sk-x")
        result = provider.generate("test")

        assert "API调用失败" in result
