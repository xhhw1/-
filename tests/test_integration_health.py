from types import SimpleNamespace

from fastapi.testclient import TestClient

from ai_visual_agent.domain import USPCandidates, USPItem
from ai_visual_agent.main import app
from ai_visual_agent.services.integration_health import (
    build_integration_health_report,
    clear_integration_events,
    record_integration_event,
)
from ai_visual_agent.services.structured_llm import get_structured_llm_provider, invoke_structured


def _settings(**overrides):
    defaults = {
        "llm_backend": "mock",
        "deepseek_api_key": None,
        "deepseek_model_strategy": "deepseek-v4-pro",
        "deepseek_model_fast": "deepseek-v4-flash",
        "multimodal_backend": "mock",
        "multimodal_model": "gemini-2.5-flash",
        "gemini_api_key": None,
        "openai_api_key": None,
        "mock_external_tools": True,
        "openai_image_model": "gpt-image-2",
        "ocr_backend": "mock",
        "segmentation_backend": "mock",
        "sam2_checkpoint": None,
        "sam2_model_cfg": None,
        "project_store_backend": "memory",
        "graph_checkpoint_backend": "memory",
        "database_url": "",
        "qdrant_url": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fallback_usps() -> USPCandidates:
    return USPCandidates(
        core=[
            USPItem(
                title="Clear play proof",
                description="Shows the core play mechanic clearly.",
                aligned_expectations=["fun"],
                product_evidence=["product image"],
            )
        ]
    )


def test_deepseek_missing_key_is_misconfigured() -> None:
    report = build_integration_health_report(
        _settings(llm_backend="deepseek", deepseek_api_key=None)
    )

    llm = next(item for item in report.items if item.name == "llm")
    assert report.status == "misconfigured"
    assert llm.status == "misconfigured"
    assert llm.missing_env == ["DEEPSEEK_API_KEY"]


def test_llm_runtime_failure_is_visible_in_health(monkeypatch) -> None:
    clear_integration_events()

    class DeepSeekSettings:
        llm_backend = "deepseek"
        deepseek_api_key = None
        deepseek_model_fast = "deepseek-v4-flash"
        deepseek_model_strategy = "deepseek-v4-pro"
        deepseek_base_url = "https://api.deepseek.com"
        llm_temperature = 0.2

    monkeypatch.setattr("ai_visual_agent.services.structured_llm.get_settings", lambda: DeepSeekSettings())
    get_structured_llm_provider.cache_clear()
    result = invoke_structured(
        schema=USPCandidates,
        prompt_name="marketer",
        context={},
        fallback=_fallback_usps(),
    )

    assert result.fallback_used is True
    assert "DEEPSEEK_API_KEY" in (result.error or "")

    report = build_integration_health_report(
        _settings(llm_backend="deepseek", deepseek_api_key="configured")
    )
    llm = next(item for item in report.items if item.name == "llm")
    assert report.status == "degraded"
    assert llm.status == "degraded"
    assert "DEEPSEEK_API_KEY" in (llm.last_error or "")
    clear_integration_events()
    get_structured_llm_provider.cache_clear()


def test_health_integrations_endpoint() -> None:
    record_integration_event(
        name="llm",
        backend="mock",
        model="mock-structured-llm",
        ok=True,
        fallback_used=True,
    )

    client = TestClient(app)
    response = client.get("/health/integrations")

    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert any(item["name"] == "llm" for item in body["items"])
    clear_integration_events()
