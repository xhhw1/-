from ai_visual_agent.config import get_settings
from ai_visual_agent.graph.state import GraphState


def route_by_workflow_type(state: GraphState) -> str:
    return "packaging" if state.get("workflow_type") == "packaging" else "detail_page"


def route_after_usp_review(state: GraphState) -> str:
    feedback = state.get("human_feedback", [])
    last = feedback[-1] if feedback else {}
    if last.get("stage") == "usp_review" and last.get("action") == "reject":
        return "regenerate"
    return route_by_workflow_type(state)


def route_after_strategy_review(state: GraphState) -> str:
    feedback = state.get("human_feedback", [])
    last = feedback[-1] if feedback else {}
    if last.get("stage") == "strategy_review" and last.get("action") == "reject":
        return route_by_workflow_type(state)
    return "continue"


def route_after_quality_check(state: GraphState) -> str:
    report = state.get("qc_report", {})
    if report.get("passed", False):
        return "pass"

    revision_round = int(state.get("revision_round", 0))
    if revision_round >= get_settings().max_revision_rounds:
        return "needs_human"

    return "revise"


def route_after_final_review(state: GraphState) -> str:
    feedback = state.get("human_feedback", [])
    if not feedback:
        return "archive"

    last = feedback[-1]
    if last.get("stage") == "final_review" and last.get("action") in {"edit", "reject"}:
        return "revise"

    return "archive"
