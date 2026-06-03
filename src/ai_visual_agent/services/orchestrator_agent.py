from __future__ import annotations

from typing import Any

from ai_visual_agent.domain import (
    AgentChatMessage,
    AgentChatRequest,
    AgentChatResponse,
    HumanReviewInput,
    ProjectDetailResponse,
    WorkflowResult,
)
from ai_visual_agent.services.audit_store import audit_store
from ai_visual_agent.services.project_detail import build_project_detail
from ai_visual_agent.services.project_store import project_store
from ai_visual_agent.services.workflow_engine import workflow_engine
from ai_visual_agent.services.workflow_requirements import workflow_requirements


START_TERMS = {"开始", "启动", "开跑", "跑流程", "进入流程", "start"}
APPROVE_TERMS = {"确认", "通过", "可以", "继续", "下一步", "没问题", "ok", "approve", "yes"}
REJECT_TERMS = {"不通过", "不可以", "退回", "重做", "重新", "不对", "有问题", "reject", "redo"}
EDIT_TERMS = {"修改", "改成", "调整", "替换", "edit"}


def list_agent_messages(project_id: str) -> list[AgentChatMessage]:
    records = audit_store.list_records(project_id=project_id, record_type="conversation")
    return [
        AgentChatMessage(
            id=record.id,
            role=record.payload.get("role", "agent"),
            message_type=record.payload.get("message_type", "text"),
            content=record.payload.get("content", ""),
            payload=record.payload.get("payload", {}),
            created_at=record.created_at,
        )
        for record in records
    ]


def run_orchestrator_turn(project_id: str, request: AgentChatRequest) -> AgentChatResponse:
    project = project_store.get(project_id)
    _record_chat_message(
        project_id=project_id,
        role="user",
        message_type="text",
        content=request.message,
        payload={"reviewer": request.reviewer, **request.payload},
    )

    detail = build_project_detail(project)
    workflow_result: WorkflowResult | None = None
    decision = _infer_decision(request.message, detail)

    if project.status == "created" and decision in {"start", "approve"}:
        requirements = workflow_requirements(project)
        if not requirements.get("ready", True):
            _record_chat_message(
                project_id=project_id,
                role="agent",
                message_type="review_gate",
                content=(
                    "包装概念流程还不能开始。请先上传必需资料："
                    + "、".join(str(item) for item in requirements.get("missing", []))
                    + "。资料齐全后，我会调用解析工具读取 PPT/PDF 和产品图，再进入卖点提炼。"
                ),
                payload={"workflow_requirements": requirements},
            )
            detail = build_project_detail(project)
            return _response(project_id, "materials_required", detail, workflow_result)
        workflow_result = workflow_engine.start(project)
        project_store.update_status(project_id, workflow_result.status)
        project = project_store.get(project_id)
        detail = build_project_detail(project)
        _record_tool_result(project_id, "workflow_start", workflow_result)
        _record_review_gate_or_status(project_id, detail, decision="workflow_started")
        return _response(project_id, "workflow_started", detail, workflow_result)

    if project.status == "waiting_review":
        if decision == "approve":
            workflow_result = _resume_review(project_id, request, action="approve")
            project_store.update_status(project_id, workflow_result.status)
            project = project_store.get(project_id)
            detail = build_project_detail(project)
            _record_tool_result(project_id, "workflow_resume_approve", workflow_result)
            _record_review_gate_or_status(project_id, detail, decision="review_approved")
            return _response(project_id, "review_approved", detail, workflow_result)

        if decision == "reject":
            workflow_result = _resume_review(project_id, request, action="reject")
            project_store.update_status(project_id, workflow_result.status)
            project = project_store.get(project_id)
            detail = build_project_detail(project)
            _record_tool_result(project_id, "workflow_resume_reject", workflow_result)
            _record_review_gate_or_status(project_id, detail, decision="review_rejected")
            return _response(project_id, "review_rejected", detail, workflow_result)

        if decision == "edit":
            _record_chat_message(
                project_id=project_id,
                role="agent",
                message_type="review_gate",
                content=(
                    "我理解你要人工修改当前审核内容。请在右侧结构化表单里改好后，"
                    "再发“确认通过”继续；如果想让 Agent 重新生成，可以直接发“退回重做”。"
                ),
                payload={"pending_review": detail.pending_review},
            )
            return _response(project_id, "manual_edit_needed", detail, workflow_result)

    if project.status == "created":
        _record_chat_message(
            project_id=project_id,
            role="agent",
            message_type="status",
            content="项目已经准备好。你可以上传素材，或直接发“开始”让我启动多智能体流程。",
            payload={"file_memory_context": detail.file_memory_context},
        )
        return _response(project_id, "waiting_to_start", detail, workflow_result)

    _record_review_gate_or_status(project_id, detail, decision="status_report")
    return _response(project_id, "status_report", detail, workflow_result)


def _resume_review(project_id: str, request: AgentChatRequest, *, action: str) -> WorkflowResult:
    review = HumanReviewInput(
        action=action,  # type: ignore[arg-type]
        reviewer=request.reviewer or "orchestrator-agent",
        comment=request.message,
        requested_changes=[request.message] if action == "reject" else [],
    )
    return workflow_engine.resume(project_id, review)


def _infer_decision(message: str, detail: ProjectDetailResponse) -> str:
    text = message.lower().strip()
    if not text:
        return "status"
    if _contains_any(text, REJECT_TERMS):
        return "reject"
    if detail.pending_review and _contains_any(text, EDIT_TERMS) and not _contains_any(text, APPROVE_TERMS):
        return "edit"
    if _contains_any(text, START_TERMS):
        return "start"
    if _contains_any(text, APPROVE_TERMS):
        return "approve"
    return "status"


def _contains_any(text: str, terms: set[str]) -> bool:
    return any(term in text for term in terms)


def _record_review_gate_or_status(project_id: str, detail: ProjectDetailResponse, *, decision: str) -> None:
    if detail.pending_review:
        pending = detail.pending_review
        _record_chat_message(
            project_id=project_id,
            role="agent",
            message_type="review_gate",
            content=_review_gate_content(pending),
            payload={
                "decision": decision,
                "pending_review": pending,
                "file_memory_context": detail.file_memory_context,
            },
        )
        return

    _record_chat_message(
        project_id=project_id,
        role="agent",
        message_type="status",
        content=_status_content(detail),
        payload={
            "decision": decision,
            "project_status": detail.project.status,
            "latest_qc_report": detail.latest_qc_report,
            "latest_generated_outputs": detail.latest_generated_outputs,
        },
    )


def _review_gate_content(pending: dict[str, Any]) -> str:
    review_type = pending.get("type")
    if review_type == "usp_review":
        return "当前卡在卖点审核。你可以说“确认通过”继续，也可以在表单里人工修改，或说“退回重做”。"
    if review_type == "strategy_review":
        return "当前卡在视觉策略审核。确认后我会进入 VI 理解和设计生成；不满意可以修改或退回重做。"
    if review_type == "final_design_review":
        return "当前卡在最终设计图审核。确认通过后会归档；不满意可以说明问题并退回生成下一版。"
    return "当前有一个人工审核点，需要确认通过、人工修改或退回重做。"


def _status_content(detail: ProjectDetailResponse) -> str:
    if detail.project.status == "completed":
        return "项目已经完成归档。你可以下载归档，或继续提出新一轮修改需求。"
    if detail.latest_generated_outputs.get("items"):
        return "设计输出已生成。请检查输出图和质检结果，再决定是否继续。"
    return f"当前项目状态：{detail.project.status}。你可以继续和我说明下一步。"


def _record_tool_result(project_id: str, stage: str, result: WorkflowResult) -> None:
    _record_chat_message(
        project_id=project_id,
        role="tool",
        message_type="tool_result",
        content=f"{stage}: {result.status}",
        payload={
            "stage": stage,
            "workflow_result": result.model_dump(mode="json"),
        },
    )


def _record_chat_message(
    *,
    project_id: str,
    role: str,
    message_type: str,
    content: str,
    payload: dict[str, Any] | None = None,
) -> AgentChatMessage:
    record = audit_store.record(
        project_id=project_id,
        record_type="conversation",
        stage="agent_chat",
        payload={
            "role": role,
            "message_type": message_type,
            "content": content,
            "payload": payload or {},
        },
    )
    return AgentChatMessage(
        id=record.id,
        role=role,  # type: ignore[arg-type]
        message_type=message_type,  # type: ignore[arg-type]
        content=content,
        payload=payload or {},
        created_at=record.created_at,
    )


def _response(
    project_id: str,
    decision: str,
    detail: ProjectDetailResponse,
    workflow_result: WorkflowResult | None,
) -> AgentChatResponse:
    return AgentChatResponse(
        project_id=project_id,
        decision=decision,
        messages=list_agent_messages(project_id),
        project_detail=detail,
        workflow_result=workflow_result,
    )
