from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ai_visual_agent.domain import (
    GoldenCheck,
    GoldenCheckResult,
    GoldenFixtureSummary,
    GoldenRunResult,
    HumanReviewInput,
    ProjectCreateRequest,
)
from ai_visual_agent.services.audit_store import audit_store
from ai_visual_agent.services.project_store import project_store
from ai_visual_agent.services.workflow_engine import workflow_engine


GOLDEN_FIXTURE_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "golden"


class GoldenFixture(BaseModel):
    name: str
    description: str = ""
    project: ProjectCreateRequest
    reviews: list[HumanReviewInput] = Field(default_factory=list)
    checks: list[GoldenCheck] = Field(default_factory=list)


def list_golden_fixtures() -> list[GoldenFixtureSummary]:
    return [
        GoldenFixtureSummary(
            name=fixture.name,
            workflow_type=fixture.project.workflow_type,
            description=fixture.description,
            check_count=len(fixture.checks),
        )
        for fixture in _load_all_fixtures()
    ]


def load_golden_fixture(name: str) -> GoldenFixture:
    if "/" in name or "\\" in name or name in {"", ".", ".."}:
        raise ValueError(f"Invalid golden fixture name: {name}")

    path = GOLDEN_FIXTURE_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Golden fixture not found: {path}")

    return GoldenFixture.model_validate(json.loads(path.read_text(encoding="utf-8")))


def run_golden_fixture(name: str, max_reviews: int = 6) -> GoldenRunResult:
    fixture = load_golden_fixture(name)
    project = project_store.create(fixture.project)
    result = workflow_engine.start(project)
    project_store.update_status(project.id, result.status)

    review_index = 0
    while result.status == "waiting_review":
        if review_index >= max_reviews:
            break
        review = (
            fixture.reviews[review_index]
            if review_index < len(fixture.reviews)
            else HumanReviewInput(action="approve", reviewer="golden", comment="auto approve")
        )
        result = workflow_engine.resume(project.id, review)
        project_store.update_status(project.id, result.status)
        review_index += 1

    agent_runs = [
        record.payload
        for record in audit_store.list_records(project_id=project.id, record_type="agent_run")
    ]
    context = {
        "status": result.status,
        "state": result.state,
        "agent_runs": agent_runs,
    }
    check_results = [evaluate_check(check, context) for check in fixture.checks]
    return GoldenRunResult(
        fixture_name=fixture.name,
        project_id=project.id,
        workflow_type=fixture.project.workflow_type,
        status=result.status,
        passed=all(check.passed for check in check_results),
        checks=check_results,
        agent_run_count=len(agent_runs),
        final_state=result.state,
    )


def evaluate_check(check: GoldenCheck, context: dict[str, Any]) -> GoldenCheckResult:
    missing = object()
    actual = _get_path(context, check.path, default=missing)
    if actual is missing:
        return GoldenCheckResult(
            **check.model_dump(),
            actual=None,
            passed=False,
            message=f"Path not found: {check.path}",
        )

    passed = _match(actual=actual, operator=check.operator, expected=check.expected)
    return GoldenCheckResult(
        **check.model_dump(),
        actual=actual,
        passed=passed,
        message="passed" if passed else f"Expected {check.operator} {check.expected!r}, got {actual!r}",
    )


def _load_all_fixtures() -> list[GoldenFixture]:
    if not GOLDEN_FIXTURE_DIR.exists():
        return []
    return [load_golden_fixture(path.stem) for path in sorted(GOLDEN_FIXTURE_DIR.glob("*.json"))]


def _get_path(value: Any, path: str, default: Any = None) -> Any:
    cursor = value
    for part in path.split("."):
        if isinstance(cursor, dict):
            if part not in cursor:
                return default
            cursor = cursor[part]
            continue
        if isinstance(cursor, list):
            try:
                cursor = cursor[int(part)]
            except (ValueError, IndexError):
                return default
            continue
        return default
    return cursor


def _match(*, actual: Any, operator: str, expected: Any) -> bool:
    if operator == "exists":
        return actual is not None and actual != "" and actual != []
    if operator == "equals":
        return actual == expected
    if operator == "contains":
        if isinstance(actual, list):
            return expected in actual or any(str(expected) in str(item) for item in actual)
        return str(expected) in str(actual)
    if operator == "startswith":
        return str(actual).startswith(str(expected))
    if operator == "min_count":
        try:
            return len(actual) >= int(expected)
        except TypeError:
            return False
    return False
