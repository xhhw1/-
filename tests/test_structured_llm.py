import sys
from types import SimpleNamespace

import pytest

from ai_visual_agent.domain import USPCandidates, USPItem
from ai_visual_agent.services.structured_llm import (
    DeepSeekStructuredLLMProvider,
    MockStructuredLLMProvider,
    get_structured_llm_provider,
    invoke_structured,
    _parse_json_object,
)


def _fallback_usps() -> USPCandidates:
    return USPCandidates(
        core=[
            USPItem(
                title="Clear play proof",
                description="Shows the core play mechanic clearly.",
                aligned_expectations=["fun"],
                product_evidence=["product image"],
                competitor_comparison="More readable than competitor hero image.",
            )
        ],
        secondary=[],
        notes=["fallback"],
    )


def test_mock_structured_llm_returns_fallback() -> None:
    fallback = _fallback_usps()

    result = MockStructuredLLMProvider().invoke(
        schema=USPCandidates,
        prompt_name="marketer",
        context={"brief": "toy"},
        fallback=fallback,
    )

    assert result.output == fallback
    assert result.backend == "mock"
    assert result.fallback_used is True


def test_invoke_structured_uses_default_mock_backend(monkeypatch) -> None:
    class MockSettings:
        llm_backend = "mock"
        deepseek_model_fast = "deepseek-fast"
        deepseek_model_strategy = "deepseek-strategy"

    monkeypatch.setattr("ai_visual_agent.services.structured_llm.get_settings", lambda: MockSettings())
    get_structured_llm_provider.cache_clear()
    fallback = _fallback_usps()

    try:
        result = invoke_structured(
            schema=USPCandidates,
            prompt_name="marketer",
            context={"brief": "toy"},
            fallback=fallback,
        )
    finally:
        get_structured_llm_provider.cache_clear()

    assert result.output.core[0].title == "Clear play proof"
    assert result.metadata()["prompt_name"] == "marketer"


def test_deepseek_provider_requires_api_key(monkeypatch) -> None:
    class EmptySettings:
        deepseek_api_key = None
        deepseek_model_fast = "deepseek-fast"
        deepseek_model_strategy = "deepseek-strategy"
        deepseek_base_url = "https://api.deepseek.com"
        llm_temperature = 0.2

    monkeypatch.setattr("ai_visual_agent.services.structured_llm.get_settings", lambda: EmptySettings())

    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        DeepSeekStructuredLLMProvider().invoke(
            schema=USPCandidates,
            prompt_name="marketer",
            context={},
            fallback=_fallback_usps(),
        )


def test_parse_json_object_accepts_fenced_or_explained_json() -> None:
    assert _parse_json_object('```json\n{"ok": true}\n```') == {"ok": True}
    assert _parse_json_object('好的，结果如下：{"ok": true, "text": "{not-end}"}') == {
        "ok": True,
        "text": "{not-end}",
    }


def test_deepseek_provider_uses_plain_chat_json(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Settings:
        deepseek_api_key = "test-key"
        deepseek_model_fast = "deepseek-fast"
        deepseek_model_strategy = "deepseek-strategy"
        deepseek_base_url = "https://example.test/v1"
        llm_temperature = 0.2

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def with_structured_output(self, _schema):
            raise AssertionError("response_format based structured output should not be used")

        def invoke(self, messages):
            captured["messages"] = messages
            return SimpleNamespace(
                content=(
                    "```json\n"
                    '{"core":[{"title":"透明拨珠吸引点","description":"透明窗能直接看到拨珠反馈。",'
                    '"aligned_expectations":["互动"],"product_evidence":["产品图：透明圆窗和拨珠"],'
                    '"competitor_comparison":"竞品证据不足，需补充主图视频对比。","confidence":0.78}],'
                    '"secondary":[],"notes":[]}'
                    "\n```"
                )
            )

    monkeypatch.setattr("ai_visual_agent.services.structured_llm.get_settings", lambda: Settings())
    monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=FakeChatOpenAI))

    result = DeepSeekStructuredLLMProvider().invoke(
        schema=USPCandidates,
        prompt_name="marketer",
        context={"brief": "婴童玩具", "asset_evidence": ["产品图"]},
        fallback=_fallback_usps(),
        model_role="fast",
    )

    assert result.fallback_used is False
    assert result.model == "deepseek-fast"
    assert result.output.core[0].title == "透明拨珠吸引点"
    assert captured["kwargs"]["base_url"] == "https://example.test/v1"  # type: ignore[index]
    messages = captured["messages"]
    assert isinstance(messages, list)
    assert "JSON Schema" in messages[1][1]


def test_deepseek_provider_retries_until_valid_json(monkeypatch) -> None:
    calls = {"count": 0}

    class Settings:
        deepseek_api_key = "test-key"
        deepseek_model_fast = "deepseek-fast"
        deepseek_model_strategy = "deepseek-strategy"
        deepseek_base_url = "https://example.test/v1"
        llm_temperature = 0.2
        llm_retry_attempts = 2
        llm_retry_backoff_seconds = 0
        llm_request_timeout = 20

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def invoke(self, messages):
            calls["count"] += 1
            if calls["count"] == 1:
                return SimpleNamespace(content="not-json")
            assert "自动重试" in messages[0][1]
            return SimpleNamespace(content='{"core":[],"secondary":[],"notes":["ok"]}')

    monkeypatch.setattr("ai_visual_agent.services.structured_llm.get_settings", lambda: Settings())
    monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=FakeChatOpenAI))

    result = DeepSeekStructuredLLMProvider().invoke(
        schema=USPCandidates,
        prompt_name="marketer",
        context={"brief": "toy"},
        fallback=_fallback_usps(),
    )

    assert result.fallback_used is False
    assert result.model == "deepseek-strategy"
    assert result.attempts == 2
    assert calls["count"] == 2
    assert result.retry_errors


def test_deepseek_provider_tries_fast_model_after_strategy_failure(monkeypatch) -> None:
    called_models: list[str] = []

    class Settings:
        deepseek_api_key = "test-key"
        deepseek_model_fast = "deepseek-fast"
        deepseek_model_strategy = "deepseek-strategy"
        deepseek_base_url = "https://example.test/v1"
        llm_temperature = 0.2
        llm_retry_attempts = 1
        llm_retry_backoff_seconds = 0
        llm_request_timeout = 20

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            self.model = kwargs["model"]

        def invoke(self, _messages):
            called_models.append(self.model)
            if self.model == "deepseek-strategy":
                raise RuntimeError("provider unavailable")
            return SimpleNamespace(content='{"core":[],"secondary":[],"notes":["fast ok"]}')

    monkeypatch.setattr("ai_visual_agent.services.structured_llm.get_settings", lambda: Settings())
    monkeypatch.setitem(sys.modules, "langchain_openai", SimpleNamespace(ChatOpenAI=FakeChatOpenAI))

    result = DeepSeekStructuredLLMProvider().invoke(
        schema=USPCandidates,
        prompt_name="marketer",
        context={"brief": "toy"},
        fallback=_fallback_usps(),
    )

    assert result.fallback_used is False
    assert result.model == "deepseek-fast"
    assert result.attempts == 2
    assert called_models == ["deepseek-strategy", "deepseek-fast"]
    assert "provider unavailable" in result.retry_errors[0]
