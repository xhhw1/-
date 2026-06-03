from fastapi.testclient import TestClient

from ai_visual_agent.main import app
from ai_visual_agent.services.prompt_registry import get_prompt_registry


def test_prompt_registry_versions_prompt_by_content_hash() -> None:
    prompt = get_prompt_registry().get("marketer", include_content=False)

    assert prompt.name == "marketer"
    assert prompt.version.startswith("marketer@")
    assert len(prompt.content_hash) == 64
    assert prompt.content == ""


def test_prompt_api_lists_and_returns_prompt_content() -> None:
    client = TestClient(app)

    list_response = client.get("/api/prompts")
    detail_response = client.get("/api/prompts/marketer")

    assert list_response.status_code == 200
    assert any(prompt["name"] == "marketer" for prompt in list_response.json())
    assert detail_response.status_code == 200
    assert detail_response.json()["content"]
