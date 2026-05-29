"""Tests for LLM provider abstraction."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.llm import ClaudeProvider, OllamaProvider, create_provider


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
