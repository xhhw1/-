from __future__ import annotations

from ai_visual_agent.domain import (
    IntegrationHealthItem,
    IntegrationProbeRequest,
    IntegrationProbeResult,
    USPCandidates,
    USPItem,
)
from ai_visual_agent.services.integration_health import build_integration_health_report
from ai_visual_agent.services.structured_llm import invoke_structured


def run_integration_probe(request: IntegrationProbeRequest) -> IntegrationProbeResult:
    """Run a low-risk provider readiness probe.

    Dry-run probes inspect configuration and local dependencies only. Active probes can execute
    the mock LLM backend immediately; real external calls require explicit opt-in.
    """

    items = _selected_items(request.target)
    messages = ["Dry-run probe checked configuration and local dependencies."]

    if request.active:
        if request.target in {"all", "llm"}:
            messages.extend(_run_llm_probe(items=items, allow_external_call=request.allow_external_call))
            items = _selected_items(request.target)
        else:
            messages.append(
                f"Active probe for {request.target} is not implemented yet; dry-run status returned."
            )

    return IntegrationProbeResult(
        target=request.target,
        status=_aggregate_status(items),
        active=request.active,
        allow_external_call=request.allow_external_call,
        items=items,
        messages=messages,
    )


def _selected_items(target: str) -> list[IntegrationHealthItem]:
    report = build_integration_health_report()
    if target == "all":
        return report.items
    return [item for item in report.items if item.name == target]


def _run_llm_probe(*, items: list[IntegrationHealthItem], allow_external_call: bool) -> list[str]:
    llm_item = next((item for item in items if item.name == "llm"), None)
    if llm_item is None:
        return ["LLM probe skipped because no llm health item was found."]
    if llm_item.status == "misconfigured":
        return [f"Active LLM probe skipped: {llm_item.message}"]
    if llm_item.backend != "mock" and not allow_external_call:
        return ["Active LLM probe skipped because allow_external_call=false."]

    result = invoke_structured(
        schema=USPCandidates,
        prompt_name="marketer",
        context={
            "probe": True,
            "expectation": "Return a minimal valid USP object for provider readiness.",
        },
        fallback=USPCandidates(
            core=[
                USPItem(
                    title="Provider probe",
                    description="Minimal fallback object used by integration readiness checks.",
                )
            ],
            secondary=[],
            notes=["integration_probe"],
        ),
        model_role="fast",
    )
    if result.error:
        return [f"Active LLM probe fell back: {result.error}"]
    if result.fallback_used and result.backend != "mock":
        return ["Active LLM probe used fallback output."]
    return ["Active LLM probe executed."]


def _aggregate_status(items: list[IntegrationHealthItem]) -> str:
    if not items:
        return "misconfigured"
    statuses = {item.status for item in items}
    if "misconfigured" in statuses:
        return "misconfigured"
    if "degraded" in statuses or "unknown" in statuses:
        return "degraded"
    return "ok"
