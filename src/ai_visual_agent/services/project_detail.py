from __future__ import annotations

from collections import Counter
from typing import Any

from ai_visual_agent.domain import (
    AuditRecord,
    ProjectDetailResponse,
    ProjectProgressSummary,
    ProjectRecord,
)
from ai_visual_agent.services.asset_memory import project_file_memory_context
from ai_visual_agent.services.audit_store import audit_store
from ai_visual_agent.services.workflow_requirements import workflow_requirements


def build_project_detail(project: ProjectRecord) -> ProjectDetailResponse:
    records = audit_store.list_records(project_id=project.id)
    latest_agent_outputs = _latest_payload_by_stage(records, "agent_output")
    latest_generated_outputs = _generated_outputs(latest_agent_outputs)
    latest_qc_report = _latest_payload(records, "qc_report", "quality_check").get("qc_report", {})
    latest_archive = _latest_payload(records, "archive_record", "archive")

    return ProjectDetailResponse(
        project=project,
        progress=_progress_summary(project, records),
        pending_review=_pending_review(project, records, latest_agent_outputs, latest_generated_outputs, latest_qc_report),
        file_memory_context=project_file_memory_context(project),
        workflow_requirements=workflow_requirements(project),
        latest_agent_outputs=latest_agent_outputs,
        latest_generated_outputs=latest_generated_outputs,
        latest_qc_report=latest_qc_report,
        latest_archive=latest_archive,
        audit_records=records,
    )


def _progress_summary(project: ProjectRecord, records: list[AuditRecord]) -> ProjectProgressSummary:
    completed = []
    seen = set()
    for record in records:
        if record.stage not in seen:
            completed.append(record.stage)
            seen.add(record.stage)

    counts = Counter(record.record_type for record in records)
    return ProjectProgressSummary(
        completed_stages=completed,
        current_stage=project.status,
        audit_count=len(records),
        human_review_count=counts.get("human_review", 0),
        agent_run_count=counts.get("agent_run", 0),
        asset_count=len(project.assets),
    )


def _pending_review(
    project: ProjectRecord,
    records: list[AuditRecord],
    latest_agent_outputs: dict[str, Any],
    latest_generated_outputs: dict[str, Any],
    latest_qc_report: dict[str, Any],
) -> dict[str, Any] | None:
    if project.status != "waiting_review":
        return None

    pending_type = _latest_pending_review_type(records)
    if pending_type == "usp_review":
        return {
            "type": "usp_review",
            "title": "请审核核心卖点和次要卖点",
            "payload": latest_agent_outputs.get("generate_usps", {}).get("usp_candidates", {}),
            "allowed_actions": ["approve", "edit", "reject"],
        }
    if pending_type == "strategy_review":
        strategy_key = "packaging_strategy" if project.workflow_type == "packaging" else "detail_page_strategy"
        stage = "packaging_strategy" if project.workflow_type == "packaging" else "detail_strategy"
        return {
            "type": "strategy_review",
            "title": "请审核视觉策略",
            "workflow_type": project.workflow_type,
            "payload": latest_agent_outputs.get(stage, {}).get(strategy_key, {}),
            "allowed_actions": ["approve", "edit", "reject"],
        }
    if pending_type == "final_design_review":
        return {
            "type": "final_design_review",
            "title": "请审核最终设计图",
            "payload": {
                "generated_outputs": latest_generated_outputs,
                "qc_report": latest_qc_report,
            },
            "allowed_actions": ["approve", "edit", "reject"],
        }
    return None


def _latest_pending_review_type(records: list[AuditRecord]) -> str | None:
    for record in reversed(records):
        if record.record_type == "qc_report" and record.stage == "quality_check":
            return "final_design_review"
        if record.record_type != "agent_output":
            continue
        if record.stage == "generate_usps":
            return "usp_review"
        if record.stage in {"packaging_strategy", "detail_strategy"}:
            return "strategy_review"
        if record.stage in {"generate_design", "generate_design_assets"}:
            return "final_design_review"
    return None


def _latest_payload_by_stage(records: list[AuditRecord], record_type: str) -> dict[str, Any]:
    payloads: dict[str, Any] = {}
    for record in records:
        if record.record_type == record_type:
            payloads[record.stage] = record.payload
    return payloads


def _latest_payload(records: list[AuditRecord], record_type: str, stage: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for record in records:
        if record.record_type == record_type and record.stage == stage:
            payload = record.payload
    return payload


def _generated_outputs(latest_agent_outputs: dict[str, Any]) -> dict[str, Any]:
    generate_design = latest_agent_outputs.get("generate_design", {})
    if isinstance(generate_design, dict) and isinstance(generate_design.get("generated_outputs"), dict):
        return generate_design["generated_outputs"]

    generate_assets = latest_agent_outputs.get("generate_design_assets", {})
    if isinstance(generate_assets, dict):
        return {
            "items": generate_assets.get("items", []),
            "revision_round": generate_assets.get("revision_round", 0),
        }
    return {}
