from typing import Any, Literal, TypedDict


class GraphState(TypedDict, total=False):
    project_id: str
    workflow_type: Literal["packaging", "detail_page"]
    project_brief: dict[str, Any]
    assets: list[dict[str, Any]]
    file_memory_context: list[dict[str, Any]]

    parsed_product: dict[str, Any]
    memory_context: list[dict[str, Any]]
    competitor_insights: dict[str, Any]

    usp_candidates: dict[str, Any]
    selected_usps: dict[str, Any]

    packaging_strategy: dict[str, Any] | None
    detail_page_strategy: dict[str, Any] | None
    vi_profile: dict[str, Any]

    generated_outputs: dict[str, Any]
    qc_report: dict[str, Any]

    human_feedback: list[dict[str, Any]]
    revision_round: int
    status: str
    archive: dict[str, Any]
