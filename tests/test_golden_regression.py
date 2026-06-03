from fastapi.testclient import TestClient

from ai_visual_agent.main import app
from ai_visual_agent.services.golden_regression import (
    list_golden_fixtures,
    run_golden_fixture,
)


def test_list_golden_fixtures() -> None:
    fixtures = list_golden_fixtures()

    names = {fixture.name for fixture in fixtures}
    assert {"packaging_toy", "detail_toy"}.issubset(names)
    assert all(fixture.check_count > 0 for fixture in fixtures)


def test_run_packaging_golden_fixture() -> None:
    result = run_golden_fixture("packaging_toy")

    assert result.passed
    assert result.status == "completed"
    assert result.agent_run_count >= 3
    assert not [check for check in result.checks if not check.passed]


def test_run_detail_golden_fixture() -> None:
    result = run_golden_fixture("detail_toy")

    assert result.passed
    assert result.status == "completed"
    assert len(result.final_state["detail_page_strategy"]["screens"]) >= 5


def test_golden_regression_api() -> None:
    client = TestClient(app)

    list_response = client.get("/api/golden/fixtures")
    run_response = client.post("/api/golden/fixtures/packaging_toy/run")

    assert list_response.status_code == 200
    assert any(item["name"] == "packaging_toy" for item in list_response.json())
    assert run_response.status_code == 200
    assert run_response.json()["passed"] is True
