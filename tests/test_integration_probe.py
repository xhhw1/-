from types import SimpleNamespace

from fastapi.testclient import TestClient

from ai_visual_agent.domain import IntegrationProbeRequest
from ai_visual_agent.main import app
from ai_visual_agent.services.integration_health import clear_integration_events
from ai_visual_agent.services.integration_probe import run_integration_probe
from ai_visual_agent.services.structured_llm import get_structured_llm_provider


def _settings(**overrides):
    defaults = {
        "llm_backend": "mock",
        "deepseek_api_key": None,
        "deepseek_model_strategy": "deepseek-v4-pro",
        "deepseek_model_fast": "deepseek-v4-flash",
        "deepseek_base_url": "https://api.deepseek.com",
        "llm_temperature": 0.2,
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


def _patch_mock_settings(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr("ai_visual_agent.services.integration_health.get_settings", lambda: settings)
    monkeypatch.setattr("ai_visual_agent.services.structured_llm.get_settings", lambda: settings)
    get_structured_llm_provider.cache_clear()
    clear_integration_events()


def test_dry_run_probe_returns_all_provider_items(monkeypatch) -> None:
    _patch_mock_settings(monkeypatch)

    result = run_integration_probe(IntegrationProbeRequest())

    assert result.status == "ok"
    assert result.active is False
    assert {item.name for item in result.items} == {
        "llm",
        "multimodal",
        "image_generation",
        "document_parser",
        "ocr",
        "segmentation",
        "persistence",
        "memory",
    }
    assert "Dry-run probe" in result.messages[0]
    clear_integration_events()
    get_structured_llm_provider.cache_clear()


def test_active_llm_probe_executes_mock_provider(monkeypatch) -> None:
    _patch_mock_settings(monkeypatch)

    result = run_integration_probe(IntegrationProbeRequest(target="llm", active=True))

    assert result.status == "ok"
    assert result.active is True
    assert len(result.items) == 1
    assert result.items[0].name == "llm"
    assert result.items[0].fallback_used is True
    assert "Active LLM probe executed." in result.messages
    clear_integration_events()
    get_structured_llm_provider.cache_clear()


def test_active_real_llm_probe_requires_explicit_external_opt_in(monkeypatch) -> None:
    settings = _settings(llm_backend="deepseek", deepseek_api_key="configured")
    monkeypatch.setattr("ai_visual_agent.services.integration_health.get_settings", lambda: settings)
    monkeypatch.setattr("ai_visual_agent.services.structured_llm.get_settings", lambda: settings)
    get_structured_llm_provider.cache_clear()
    clear_integration_events()

    result = run_integration_probe(IntegrationProbeRequest(target="llm", active=True))

    assert result.status == "ok"
    assert result.items[0].backend == "deepseek"
    assert "allow_external_call=false" in result.messages[-1]
    clear_integration_events()
    get_structured_llm_provider.cache_clear()


def test_integration_probe_endpoint(monkeypatch) -> None:
    _patch_mock_settings(monkeypatch)

    client = TestClient(app)
    response = client.post(
        "/api/integrations/probe",
        json={"target": "llm", "active": True, "allow_external_call": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["target"] == "llm"
    assert body["status"] == "ok"
    assert body["items"][0]["name"] == "llm"
    assert "Active LLM probe executed." in body["messages"]
    clear_integration_events()
    get_structured_llm_provider.cache_clear()
