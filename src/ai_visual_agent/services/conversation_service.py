from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import (
    AssetKind,
    AssetRef,
    ConversationBatchDeleteResult,
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationMessage,
    ConversationMessageCreateRequest,
    ConversationReviewActionRequest,
    ConversationReviewGate,
    ProjectBrief,
    ProjectCreateRequest,
    ProjectRecord,
    ProjectUpdateRequest,
)
from ai_visual_agent.services.audit_store import audit_store
from ai_visual_agent.services.asset_memory import register_asset_memory
from ai_visual_agent.services.asset_processing import (
    cancel_asset_processing,
    mark_processing_queued,
    parser_version_for_tool,
    reuse_completed_cache_for_asset,
    uploaded_processing_patch,
)
from ai_visual_agent.services.asset_intelligence import (
    enrich_project_assets,
    evidence_summary_for_review,
    build_project_evidence_context,
)
from ai_visual_agent.services.conversation_agents import (
    _image_reference_prompt_context,
    _image_generation_reference_asset_ids,
    _product_reference_asset_ids,
    run_design_agent,
    run_detail_strategy_agent,
    run_packaging_image_prompt_agent,
    run_packaging_strategy_agent,
    run_planner_agent,
    run_usp_agent,
    run_usp_agent_result,
    run_vi_understanding_agent,
)
from ai_visual_agent.services.conversation_store import conversation_store
from ai_visual_agent.services.memory_store import get_memory_store
from ai_visual_agent.services.project_store import project_store
from ai_visual_agent.services.storage import asset_storage
from ai_visual_agent.services.task_queue import (
    TaskCancelledError,
    background_task_queue,
    raise_if_current_job_cancelled,
    register_background_handler,
)


def _owner_id(value: str | None = None) -> str:
    return (value or get_settings().admin_email or "local-admin").strip().lower()


def _assert_session_owner(session: Any, owner_id: str | None = None) -> None:
    if owner_id and _owner_id(getattr(session, "owner_id", "")) != _owner_id(owner_id):
        raise ValueError("You do not have access to this conversation.")


def _assert_project_owner(project: ProjectRecord, owner_id: str | None = None) -> None:
    if owner_id and _owner_id(project.owner_id) != _owner_id(owner_id):
        raise ValueError("You do not have access to this project.")


def create_conversation(request: ConversationCreateRequest, *, owner_id: str = "") -> ConversationDetailResponse:
    owner = _owner_id(owner_id)
    workflow_type = request.workflow_type if request.workflow_type in {"packaging", "detail_page"} else "packaging"
    brief = _brief_from_text(request.initial_message)
    project = project_store.create(
        ProjectCreateRequest(
            owner_id=owner,
            workflow_type=workflow_type,
            brief=brief,
            assets=[],
        )
    )
    session = conversation_store.create_session(
        project_id=project.id,
        owner_id=owner,
        title=request.title or _title_from_brief(brief, fallback=request.initial_message),
        workflow_type=request.workflow_type,
    )
    if request.initial_message.strip():
        handle_user_message(session.id, ConversationMessageCreateRequest(content=request.initial_message), owner_id=owner)
    return get_conversation_detail(session.id, owner_id=owner)


def list_conversations(*, owner_id: str | None = None) -> list[ConversationDetailResponse]:
    owner = _owner_id(owner_id) if owner_id else None
    _recover_project_backed_sessions(owner_id=owner)
    details: list[ConversationDetailResponse] = []
    for session in conversation_store.list_sessions(owner_id=owner):
        try:
            details.append(get_conversation_detail(session.id, auto_refresh_blocked_design=False, owner_id=owner))
        except KeyError:
            continue
    return sorted(details, key=_conversation_list_rank)


def _conversation_list_rank(detail: ConversationDetailResponse) -> tuple[int, str]:
    """Show useful work first without deleting old or noisy local records."""

    score = 0
    real_outputs = _existing_generated_output_count(detail)
    missing_outputs = _missing_placeholder_output_count(detail)
    message_count = len(detail.messages)
    asset_count = len(detail.assets)
    approved_count = sum(1 for gate in detail.review_gates if gate.status in {"approved", "edited"})

    if real_outputs:
        score += 10_000 + real_outputs * 100
    if detail.session.current_stage in {"completed", "final_design_review"}:
        score += 800
    if asset_count:
        score += min(asset_count, 12) * 40
    if approved_count:
        score += min(approved_count, 10) * 25
    if message_count > 2:
        score += min(message_count, 40)
    if missing_outputs and not real_outputs:
        score -= 3_000
    if _looks_like_local_test_session(detail) and not real_outputs:
        score -= 5_000
    if message_count <= 2 and not asset_count and not real_outputs:
        score -= 1_000

    return (-score, _reverse_time_string(detail.session.updated_at))


def _reverse_time_string(value: Any) -> str:
    text = str(value or "")
    # Good enough for ISO timestamps: descending time via ascending lexicographic rank.
    return "".join(chr(255 - ord(char)) for char in text)


def _existing_generated_output_count(detail: ConversationDetailResponse) -> int:
    asset_ids = {asset.id for asset in detail.assets}
    count = 0
    for item in _generated_output_items(detail):
        asset_id = str(item.get("asset_id") or "")
        uri = str(item.get("uri") or "")
        if asset_id and asset_id in asset_ids:
            count += 1
        elif _uri_exists(uri):
            count += 1
    return count


def _missing_placeholder_output_count(detail: ConversationDetailResponse) -> int:
    count = 0
    for item in _generated_output_items(detail):
        uri = str(item.get("uri") or "")
        asset_id = str(item.get("asset_id") or "")
        if asset_id == "asset-front" or uri.replace("\\", "/").endswith("data/assets/asset-front.png"):
            count += 1
    return count


def _generated_output_items(detail: ConversationDetailResponse) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for gate in detail.review_gates:
        if gate.type != "final_design_review":
            continue
        outputs = gate.payload.get("generated_outputs") if isinstance(gate.payload, dict) else None
        raw_items = outputs.get("items") if isinstance(outputs, dict) else None
        if isinstance(raw_items, list):
            items.extend(item for item in raw_items if isinstance(item, dict))
    return items


def _uri_exists(uri: str) -> bool:
    if not uri:
        return False
    if uri.startswith(("http://", "https://")):
        return True
    if uri.startswith("data:"):
        return True
    path = Path(uri)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return path.exists() and path.is_file()
    except OSError:
        return False


def _looks_like_local_test_session(detail: ConversationDetailResponse) -> bool:
    title = (detail.session.title or "").strip().lower()
    test_titles = {
        "interactive toy set",
        "toy packaging project, extra",
        "toy packaging project for fa",
        "cyber mechanical pet",
        "baby spinning toy",
        "this is a toy packaging proj",
        "i want packaging for a kids",
        "product intro",
    }
    if title in test_titles or title.startswith("toy packaging project"):
        return True
    return any(message.payload.get("recovered_from_project_store") for message in detail.messages)


def _recover_project_backed_sessions(*, owner_id: str | None = None) -> None:
    existing_project_ids = {session.project_id for session in conversation_store.list_sessions(owner_id=owner_id)}
    for project in project_store.list(owner_id=owner_id):
        if project.id in existing_project_ids:
            continue
        session = conversation_store.create_session(
            project_id=project.id,
            owner_id=project.owner_id,
            title=_title_from_project(project),
            workflow_type=project.workflow_type,
        )
        conversation_store.update_session(
            session.id,
            current_stage="completed" if project.status == "completed" else "collecting_input",
            status="active",
        )
        conversation_store.add_message(
            session_id=session.id,
            role="system",
            message_type="status",
            content="该项目已从数据库项目表恢复到前端列表。项目资料和素材仍可继续使用；如需继续当前流程，请发送下一步指令。",
            payload={"recovered_from_project_store": True, "project_id": project.id},
        )
        existing_project_ids.add(project.id)


def get_conversation_detail(
    session_id: str,
    *,
    auto_refresh_blocked_design: bool = True,
    owner_id: str | None = None,
) -> ConversationDetailResponse:
    session = conversation_store.get_session(session_id)
    _assert_session_owner(session, owner_id)
    project = project_store.get(session.project_id)
    _assert_project_owner(project, owner_id)
    if auto_refresh_blocked_design and _refresh_blocked_design_gate_if_references_exist(
        session_id=session_id,
        project=project,
    ):
        session = conversation_store.get_session(session_id)
        project = project_store.get(session.project_id)
    refreshed_title = _title_from_project(project)
    if _should_update_session_title(session.title, refreshed_title):
        session = conversation_store.update_session(session_id, title=refreshed_title)
    gates = conversation_store.list_review_gates(session_id)
    return ConversationDetailResponse(
        session=session,
        project=project,
        messages=_messages_for_response(conversation_store.list_messages(session_id)),
        review_gates=gates,
        pending_review_gate=conversation_store.pending_review_gate(session_id),
        confirmed_context=session.confirmed_context,
        assets=project.assets,
    )


def delete_conversation(session_id: str, *, owner_id: str | None = None) -> None:
    session = conversation_store.get_session(session_id)
    _assert_session_owner(session, owner_id)
    delete_project_workspace(session.project_id, owner_id=owner_id)


def delete_conversations_batch(session_ids: list[str], *, owner_id: str | None = None) -> ConversationBatchDeleteResult:
    requested_ids = [session_id for session_id in dict.fromkeys(session_ids) if session_id]
    result = ConversationBatchDeleteResult(requested_count=len(requested_ids))
    deleted_projects: set[str] = set()
    for session_id in requested_ids:
        try:
            session = conversation_store.get_session(session_id)
            _assert_session_owner(session, owner_id)
            if session.project_id in deleted_projects:
                continue
            delete_project_workspace(session.project_id, owner_id=owner_id)
            deleted_projects.add(session.project_id)
            result.deleted_count += 1
            result.deleted_project_ids.append(session.project_id)
        except KeyError as exc:
            result.errors.append({"session_id": session_id, "error": str(exc)})
        except ValueError as exc:
            result.errors.append({"session_id": session_id, "error": str(exc)})
    return result


def delete_project_workspace(project_id: str, *, owner_id: str | None = None) -> None:
    project = project_store.get(project_id)
    _assert_project_owner(project, owner_id)
    for asset in project.assets:
        try:
            cancel_asset_processing(project_id, asset.id, reason="project_deleted")
        except Exception:
            continue
    for session in list(conversation_store.list_sessions(owner_id=owner_id)):
        if session.project_id == project_id:
            try:
                conversation_store.delete_session(session.id)
            except KeyError:
                continue
    try:
        asset_storage.delete_project_assets(project_id)
        audit_store.delete_project_records(project_id)
        _delete_project_memory(project_id)
        project_store.delete(project_id)
    except KeyError:
        return


def _delete_project_memory(project_id: str) -> None:
    try:
        store = get_memory_store()
        delete_project = getattr(store, "delete_project", None)
        if callable(delete_project):
            delete_project(project_id)
    except Exception:
        return


def _messages_for_response(messages: list[ConversationMessage]) -> list[ConversationMessage]:
    seen_client_ids: set[str] = set()
    visible: list[ConversationMessage] = []
    hiding_retry_followup = False
    for message in messages:
        if message.role == "user":
            client_message_id = _client_message_id(message.payload)
            if client_message_id and client_message_id in seen_client_ids:
                hiding_retry_followup = True
                continue
            if client_message_id:
                seen_client_ids.add(client_message_id)
            hiding_retry_followup = False
            visible.append(message)
            continue
        if (
            hiding_retry_followup
            and message.role in {"agent", "tool"}
            and message.message_type in {"planner_decision", "status"}
        ):
            continue
        visible.append(message)
    return visible


def _bind_assets_from_message(*, project_id: str, message_id: str, content: str, payload: dict[str, Any]) -> None:
    try:
        project = project_store.get(project_id)
    except KeyError:
        return
    explicit_mentions = payload.get("mentions") if isinstance(payload.get("mentions"), list) else []
    explicit_by_id = {
        str(item.get("asset_id")): str(item.get("role_as") or item.get("role") or "")
        for item in explicit_mentions
        if isinstance(item, dict) and item.get("asset_id")
    }
    for asset in project.assets:
        role = _role_from_text_mention(content, asset) or explicit_by_id.get(asset.id)
        if not role:
            continue
        bindings = asset.metadata.get("role_bindings") if isinstance(asset.metadata.get("role_bindings"), list) else []
        bindings = [item for item in bindings if not (isinstance(item, dict) and item.get("message_id") == message_id and item.get("role") == role)]
        bindings.append(
            {
                "role": role,
                "source": "user_mention",
                "message_id": message_id,
                "confidence": 1.0,
                "active": True,
            }
        )
        project_store.update_asset_metadata(project_id, asset.id, {"role_bindings": bindings})


def _role_from_text_mention(content: str, asset: AssetRef) -> str:
    marker_positions = _asset_mention_positions(content, asset)
    if not marker_positions:
        return ""
    lowered = content.lower()
    for position in marker_positions:
        role = _nearest_role_label(lowered, position)
        if role:
            return role
    return ""


def _nearest_role_label(lowered_content: str, mention_position: int) -> str:
    role_terms = {
        "logo": ["logo", "标志", "商标"],
        "vi_reference": ["vi", "品牌规范", "视觉规范", "品牌参考"],
        "competitor_info": ["竞品图", "竞品资料", "竞品", "竞对", "对手", "爆款", "竞争"],
        "product_image": [
            "产品拼装图",
            "产品收藏系列",
            "产品系列",
            "产品参考图",
            "产品图",
            "产品图片",
            "参考图",
            "主图",
            "效果图",
            "拼装图",
            "系列图",
            "收藏系列",
            "产品外观",
        ],
        "product_intro": ["产品介绍ppt", "产品介绍", "产品资料", "产品ppt", "产品 pdf", "产品文档", "介绍"],
    }
    before = lowered_content[max(0, mention_position - 40): mention_position]
    best: tuple[int, str] | None = None
    for role, terms in role_terms.items():
        for term in terms:
            index = before.rfind(term)
            if index >= 0 and (best is None or index > best[0]):
                best = (index, role)
    if best:
        return best[1]
    after = lowered_content[mention_position: mention_position + 36]
    for role, terms in role_terms.items():
        if any(term in after for term in terms):
            return role
    return ""


def _asset_mention_positions(content: str, asset: AssetRef) -> list[int]:
    exact_candidates = [
        asset.filename,
        str(asset.metadata.get("display_name") or ""),
        str(asset.metadata.get("original_filename") or ""),
    ]
    stem = re.sub(r"\.[A-Za-z0-9]+$", "", asset.filename)
    stem_candidates = [stem] if stem and stem != asset.filename else []
    exact_positions = _mention_positions_for_candidates(content, exact_candidates)
    if exact_positions:
        return exact_positions
    return _mention_positions_for_candidates(content, stem_candidates)


def _mention_positions_for_candidates(content: str, candidates: list[str]) -> list[int]:
    positions: list[int] = []
    for candidate in {item for item in candidates if item}:
        for marker in [f"@{candidate}", f"@{candidate.strip()}"]:
            start = 0
            while True:
                index = content.find(marker, start)
                if index < 0:
                    break
                positions.append(index)
                start = index + len(marker)
    return sorted(set(positions))


def _client_message_id(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get("client_message_id") or payload.get("clientMessageId")
    return str(value).strip() if value else ""


def _has_seen_client_message(session_id: str, client_message_id: str) -> bool:
    if not client_message_id:
        return False
    for message in conversation_store.list_messages(session_id):
        if message.role != "user":
            continue
        if _client_message_id(message.payload) == client_message_id:
            return True
    return False


def handle_user_message(
    session_id: str,
    request: ConversationMessageCreateRequest,
    *,
    owner_id: str | None = None,
) -> ConversationDetailResponse:
    session = conversation_store.get_session(session_id)
    _assert_session_owner(session, owner_id)
    if _has_seen_client_message(session_id, _client_message_id(request.payload)):
        return get_conversation_detail(session_id, owner_id=owner_id)
    project = project_store.get(session.project_id)
    _assert_project_owner(project, owner_id)
    message = conversation_store.add_message(
        session_id=session_id,
        role="user",
        message_type="text",
        content=request.content,
        payload=request.payload,
    )
    _bind_assets_from_message(
        project_id=session.project_id,
        message_id=message.id,
        content=request.content,
        payload=request.payload,
    )
    merged_brief = _update_project_brief_from_message(project, request.content)
    if merged_brief:
        candidate_title = _title_from_brief(merged_brief, fallback=request.content)
        if _should_update_session_title(session.title, candidate_title):
            conversation_store.update_session(session_id, title=candidate_title)
    session = _apply_basic_workflow_inference(session_id, request.content)
    project = project_store.get(session.project_id)
    pending = conversation_store.pending_review_gate(session_id)
    if pending:
        handled = _handle_pending_review_message(
            session_id=session_id,
            project=project,
            gate=pending,
            content=request.content,
        )
        if handled:
            return handled
    decision = run_planner_agent(
        user_message=request.content,
        session=session,
        project=project,
        messages=conversation_store.list_messages(session_id),
        pending_review_gate=pending,
    )
    if decision.state_patch.get("workflow_type") in {"packaging", "detail_page"}:
        session = conversation_store.update_session(
            session_id,
            workflow_type=decision.state_patch["workflow_type"],
        )
        project_store.update(
            project.id,
            ProjectUpdateRequest(workflow_type=decision.state_patch["workflow_type"]),
        )
        project = project_store.get(project.id)

    conversation_store.add_message(
        session_id=session_id,
        role="agent",
        message_type="planner_decision",
        content=decision.message_to_user or "我已判断下一步。",
        payload={"planner_decision": decision.model_dump(mode="json"), "source_message_id": message.id},
    )

    if not pending and decision.next_action == "call_agent":
        target_agent = _guard_agent_order(session=session, requested_agent=decision.target_agent)
        _dispatch_target_agent(
            session_id=session_id,
            project=project,
            target_agent=target_agent,
            source_message=request.content,
            reason="planner_call_agent",
        )

    return get_conversation_detail(session_id, owner_id=owner_id)


REVIEW_APPROVE_TERMS = {"确认", "通过", "可以", "继续", "下一步", "没问题", "ok", "approve", "yes"}
REVIEW_REVISION_TERMS = {
    "修改",
    "改成",
    "调整",
    "优化",
    "重做",
    "重新",
    "不满意",
    "不对",
    "有问题",
    "不要",
    "别",
    "增加",
    "减少",
    "强调",
    "突出",
    "弱化",
    "换成",
    "想要",
    "修正",
    "再来",
}


def _handle_pending_review_message(
    *,
    session_id: str,
    project: ProjectRecord,
    gate: ConversationReviewGate,
    content: str,
) -> ConversationDetailResponse | None:
    text = content.strip()
    if not text:
        return None
    if _is_review_approval_text(text):
        resolved = conversation_store.resolve_review_gate(
            session_id=session_id,
            gate_id=gate.id,
            status="approved",
            payload=gate.payload,
        )
        _commit_review_payload(session_id=session_id, gate=resolved, payload=gate.payload)
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content="已按你的消息确认当前结果，继续进入下一步。",
            payload={"confirmed_gate_id": gate.id, "confirmed_gate_type": gate.type},
        )
        _continue_after_review(session_id=session_id, project=project, gate=resolved)
        return get_conversation_detail(session_id)

    if _is_review_revision_text(text):
        return _revise_pending_review_from_message(
            session_id=session_id,
            project=project,
            gate=gate,
            user_request=text,
        )
    return None


def _is_review_approval_text(text: str) -> bool:
    lowered = text.lower().strip()
    has_approve = any(term in lowered for term in REVIEW_APPROVE_TERMS)
    has_revision = any(term in lowered for term in REVIEW_REVISION_TERMS)
    return has_approve and not has_revision


def _is_review_revision_text(text: str) -> bool:
    lowered = text.lower().strip()
    if not lowered:
        return False
    if any(term in lowered for term in REVIEW_REVISION_TERMS):
        return True
    if any(term in lowered for term in {"为什么", "说明", "解释", "怎么看", "是什么"}):
        return False
    return len(lowered) >= 4


def _revise_pending_review_from_message(
    *,
    session_id: str,
    project: ProjectRecord,
    gate: ConversationReviewGate,
    user_request: str,
) -> ConversationDetailResponse:
    resolved = conversation_store.resolve_review_gate(
        session_id=session_id,
        gate_id=gate.id,
        status="rejected",
        payload=gate.payload,
    )
    retry_agent = _agent_for_review_gate(resolved)
    source_message = _revision_source_message(gate=resolved, user_request=user_request)
    conversation_store.add_message(
        session_id=session_id,
        role="agent",
        message_type="status",
        content=f"收到，我会按你的修正意见重新输出「{_review_gate_type_label(gate.type)}」。",
        payload={
            "revision_request": user_request,
            "rejected_gate_id": gate.id,
            "retry_agent": retry_agent,
        },
    )
    if retry_agent:
        if _should_run_agent_in_background(retry_agent):
            conversation_store.add_message(
                session_id=session_id,
                role="agent",
                message_type="status",
                content="已重新调度出图 Agent，生成会在后台继续，审核卡会自动刷新。",
                payload={"next_agent": retry_agent, "background": True, "revision_request": user_request},
            )
            _start_agent_background(
                session_id=session_id,
                project_id=project.id,
                target_agent=retry_agent,
                source_message=source_message,
            )
        else:
            _run_target_agent(
                session_id=session_id,
                project=project_store.get(project.id),
                target_agent=retry_agent,
                source_message=source_message,
            )
    return get_conversation_detail(session_id)


def _revision_source_message(*, gate: ConversationReviewGate, user_request: str) -> str:
    previous = json.dumps(gate.payload, ensure_ascii=False, indent=2)
    if len(previous) > 6000:
        previous = previous[:6000] + "\n...（上一版结果已截断）"
    return (
        f"用户对上一版「{_review_gate_type_label(gate.type)}」不满意，需要重新输出。\n"
        f"用户修正意见：{user_request}\n"
        "请在保留已确认上游信息和证据约束的前提下，优先满足用户修正意见；"
        "不要直接复制上一版结果。\n"
        f"上一版结果 JSON：\n{previous}"
    )


def _review_gate_type_label(gate_type: str) -> str:
    return {
        "usp_review": "卖点提炼结果",
        "vi_review": "VI 理解结果",
        "packaging_strategy_review": "包装策略",
        "detail_strategy_review": "详情页策略",
        "image_prompt_review": "主图生图提示词",
        "final_design_review": "生成图结果",
    }.get(gate_type, "当前结果")


def _guard_agent_order(*, session: Any, requested_agent: str) -> str:
    confirmed = session.confirmed_context or {}
    if not confirmed.get("confirmed_usps"):
        return requested_agent
    strategy_agents = {"strategy_agent", "packaging_strategy_agent", "detail_page_strategy_agent"}
    designer_agents = {"packaging_designer_agent", "detail_designer_agent"}
    if requested_agent in strategy_agents | designer_agents and not confirmed.get("confirmed_vi_profile"):
        return "vi_understanding_agent"
    if requested_agent in designer_agents:
        strategy_key = (
            "confirmed_detail_page_strategy"
            if session.workflow_type == "detail_page"
            else "confirmed_packaging_strategy"
        )
        if not confirmed.get(strategy_key):
            return "detail_page_strategy_agent" if session.workflow_type == "detail_page" else "packaging_strategy_agent"
    return requested_agent


async def upload_conversation_asset(
    *,
    session_id: str,
    kind: AssetKind,
    upload: Any,
    owner_id: str | None = None,
) -> ConversationDetailResponse:
    session = conversation_store.get_session(session_id)
    _assert_session_owner(session, owner_id)
    project = project_store.get(session.project_id)
    _assert_project_owner(project, owner_id)
    asset = await asset_storage.save_upload(project_id=session.project_id, kind=kind, upload=upload)
    project_store.add_asset(session.project_id, asset)
    project = project_store.get(session.project_id)
    memory = register_asset_memory(project, asset)
    processing_patch = uploaded_processing_patch(asset)
    updated = project_store.update_asset_metadata(
        project_id=session.project_id,
        asset_id=asset.id,
        metadata_patch={"asset_memory": memory, **processing_patch},
    )
    cached, _cache_patch = reuse_completed_cache_for_asset(session.project_id, updated)
    if cached:
        updated = cached
    updated = next(
        (item for item in project_store.get(session.project_id).assets if item.id == updated.id),
        updated,
    )
    return get_conversation_detail(session_id, owner_id=owner_id)


def _refresh_pending_gate_after_asset_upload(
    *,
    session_id: str,
    session: Any,
    kind: AssetKind,
    asset_id: str,
) -> None:
    pending = conversation_store.pending_review_gate(session_id)
    if pending and pending.type == "usp_review" and kind in {
        "product_ppt",
        "product_pdf",
        "product_image",
        "transparent_product_image",
        "competitor_image",
        "competitor_packaging",
        "competitor_detail_page",
    }:
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content="已收到新的产品/竞品资料。资料已保存为项目资产，不会立即解析；后续 Agent 需要时会按需读取或解析。",
            payload={"uploaded_asset_id": asset_id, "refresh_gate_type": "usp_review"},
        )
    elif pending and pending.type == "vi_review" and kind in {"vi_document", "logo"}:
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content="\u5df2\u6536\u5230 VI/LOGO \u8d44\u6599\uff0c\u4e0d\u4f1a\u7acb\u5373\u89e3\u6790\uff1b\u8bf7\u5728\u5bf9\u8bdd\u4e2d\u7ee7\u7eed\u6216\u56de\u9000\u91cd\u65b0\u5206\u6790\u3002",
            payload={"uploaded_asset_id": asset_id, "refresh_gate_type": "vi_review"},
        )
    elif pending and pending.type == "final_design_review" and kind in {
        "product_image",
        "transparent_product_image",
        "vi_document",
        "logo",
    }:
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content="\u5df2\u6536\u5230\u65b0\u53c2\u8003\u56fe\uff0c\u8d44\u6599\u5df2\u4fdd\u5b58\uff1b\u5982\u9700\u91cd\u65b0\u51fa\u56fe\uff0c\u8bf7\u56de\u9000\u6216\u660e\u786e\u8981\u6c42\u91cd\u65b0\u751f\u6210\u3002",
            payload={"uploaded_asset_id": asset_id, "refresh_gate_type": "final_design_review"},
        )


def _add_asset_intelligence_messages(*, session_id: str, reports: list[dict[str, Any]]) -> None:
    for report in reports:
        if report.get("status") == "cached":
            continue
        tool = report.get("tool") or "tool"
        filename = report.get("filename") or "资料"
        if report.get("status") == "completed":
            if tool == "document_parser":
                content = f"资料解析完成：{filename}（{report.get('parser') or 'parser'}，{report.get('page_count') or 0} 页）"
            elif tool == "image_understanding":
                content = f"图片理解完成：{filename}（{report.get('role') or 'image'}，{report.get('engine') or 'model'}）"
            else:
                content = f"工具处理完成：{filename}"
        elif report.get("status") == "failed":
            content = f"资料处理失败：{filename}（{report.get('error') or 'unknown error'}）"
        else:
            continue
        conversation_store.add_message(
            session_id=session_id,
            role="tool",
            message_type="tool_result",
            content=content,
            payload={"asset_intelligence": report},
        )


AGENT_DEPENDENCY_MATRIX: dict[str, dict[str, Any]] = {
    "usp_agent": {
        "hard": ["product_intro"],
        "soft": ["competitor_info", "product_image"],
        "fallback": {
            "competitor_info": "未检测到竞品资料，将先基于自家产品提炼卖点，并在输出中注明未结合竞品对比。",
            "product_image": "未检测到产品图，将仅基于文档和文字资料提炼卖点。",
        },
    },
    "vi_understanding_agent": {
        "hard": [],
        "soft": ["logo", "vi_reference"],
        "fallback": {
            "logo": "未检测到 LOGO，VI Agent 会生成确认卡，请你确认是否按无 LOGO/无品牌规范继续。",
            "vi_reference": "未检测到 VI 规范，将采用标准极简版式和保守色系，并在审核卡中等待你确认是否继续。",
        },
    },
    "packaging_strategy_agent": {
        "hard": ["selling_points", "product_intro", "product_dimensions"],
        "soft": ["vi_rules"],
        "fallback": {
            "vi_rules": "未检测到已确认 VI 规则，将按无品牌规范的基础电商包装策略输出。",
        },
    },
    "detail_page_strategy_agent": {
        "hard": ["selling_points", "product_intro"],
        "soft": ["vi_rules", "competitor_info", "product_image"],
        "fallback": {
            "vi_rules": "未检测到已确认 VI 规则，将按基础电商详情页版式输出。",
            "competitor_info": "未检测到竞品资料，将先基于自家产品信息组织详情页。",
            "product_image": "未检测到产品图，详情页策略会避免指定精确产品角度。",
        },
    },
    "packaging_image_prompt_agent": {
        "hard": ["packaging_strategy"],
        "soft": ["product_image", "logo"],
        "fallback": {
            "product_image": "未检测到产品图，提示词只能描述外盒设计，不会要求产品实物合成。",
            "logo": "未检测到 LOGO，提示词会只预留品牌识别区域，不虚构具体 LOGO。",
        },
    },
    "packaging_designer_agent": {
        "hard": ["packaging_strategy", "image_prompt"],
        "soft": ["product_image", "logo"],
        "fallback": {
            "product_image": "未检测到产品参考图，出图 Agent 将无法进行可靠图生图，建议补充产品图后再生成。",
            "logo": "未检测到 LOGO，生成图将只保留品牌位，不虚构 LOGO。",
        },
    },
}


DEPENDENCY_LABELS = {
    "product_intro": "产品介绍资料",
    "competitor_info": "竞品资料",
    "product_image": "产品参考图",
    "logo": "LOGO",
    "logo_or_no_brand_confirmation": "LOGO 或无品牌确认",
    "vi_reference": "VI 规范资料",
    "selling_points": "已确认卖点",
    "vi_rules": "已确认 VI 规则",
    "product_dimensions": "产品尺寸/配件信息",
    "packaging_strategy": "已确认包装策略",
    "image_prompt": "已确认主图生图提示词",
}


def _check_dependencies_before_agent(*, session_id: str, project: ProjectRecord, target_agent: str) -> bool:
    matrix = AGENT_DEPENDENCY_MATRIX.get(target_agent)
    if not matrix:
        return True
    session = conversation_store.get_session(session_id)
    missing_hard = [
        dependency
        for dependency in matrix.get("hard", [])
        if not _dependency_available(dependency=dependency, session_id=session_id, session=session, project=project)
    ]
    missing_soft = [
        dependency
        for dependency in matrix.get("soft", [])
        if not _dependency_available(dependency=dependency, session_id=session_id, session=session, project=project)
    ]
    if missing_hard:
        reason = "、".join(DEPENDENCY_LABELS.get(item, item) for item in missing_hard)
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content=f"当前节点暂不能继续：缺少{reason}。请在输入框补充说明或上传对应文件后再继续。",
            payload={
                "target_agent": target_agent,
                "allow_run": False,
                "missing_hard": missing_hard,
                "missing_soft": missing_soft,
                "dependency_matrix": matrix,
            },
        )
        return False
    if missing_soft:
        fallbacks = matrix.get("fallback", {})
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content="；".join(fallbacks.get(item, f"缺少{DEPENDENCY_LABELS.get(item, item)}，将采用保守策略。") for item in missing_soft),
            payload={
                "target_agent": target_agent,
                "allow_run": True,
                "missing_soft": missing_soft,
                "dependency_matrix": matrix,
            },
        )
    return True


def _dependency_available(*, dependency: str, session_id: str, session: Any, project: ProjectRecord) -> bool:
    confirmed = session.confirmed_context or {}
    if dependency == "selling_points":
        return bool(confirmed.get("confirmed_usps"))
    if dependency == "vi_rules":
        return bool(confirmed.get("confirmed_vi_profile"))
    if dependency == "packaging_strategy":
        return bool(confirmed.get("confirmed_packaging_strategy"))
    if dependency == "image_prompt":
        return bool(confirmed.get("confirmed_image_prompt"))
    if dependency == "product_intro":
        return bool(project.brief.core_product_definition or project.brief.raw_text or _assets_with_roles_or_kinds(project, {"product_intro"}, {"product_ppt", "product_pdf"}))
    if dependency == "product_dimensions":
        return _has_product_dimensions(project) or bool(_assets_with_roles_or_kinds(project, {"product_intro"}, {"product_ppt", "product_pdf"}))
    if dependency == "competitor_info":
        return bool(_assets_with_roles_or_kinds(project, {"competitor_info"}, {"competitor_image", "competitor_packaging", "competitor_detail_page"}))
    if dependency == "product_image":
        return bool(_product_reference_asset_ids(project))
    if dependency == "logo":
        return bool(_assets_with_roles_or_kinds(project, {"logo"}, {"logo"}))
    if dependency == "logo_or_no_brand_confirmation":
        return bool(_assets_with_roles_or_kinds(project, {"logo"}, {"logo"})) or _conversation_confirms_no_brand(session_id)
    if dependency == "vi_reference":
        return bool(_assets_with_roles_or_kinds(project, {"vi_reference"}, {"vi_document"}))
    return True


def _refresh_blocked_design_gate_if_references_exist(*, session_id: str, project: ProjectRecord) -> bool:
    pending = conversation_store.pending_review_gate(session_id)
    if not pending or pending.type != "final_design_review":
        return False
    payload = pending.payload or {}
    if not payload.get("generation_blocked"):
        return False
    required_assets = payload.get("required_assets") if isinstance(payload.get("required_assets"), list) else []
    reason = str(payload.get("reason") or "")
    if "product_image" not in required_assets and "产品参考图" not in reason:
        return False
    reference_ids = _product_reference_asset_ids(project)
    if not reference_ids:
        return False
    repaired_payload = {
        **payload,
        "generation_blocked": False,
        "stale_block_resolved": True,
        "resolved_reference_asset_ids": reference_ids,
    }
    conversation_store.resolve_review_gate(
        session_id=session_id,
        gate_id=pending.id,
        status="needs_more_info",
        payload=repaired_payload,
    )
    target_agent = pending.next_step_on_approve or "packaging_designer_agent"
    conversation_store.add_message(
        session_id=session_id,
        role="agent",
        message_type="status",
        content="已检测到项目中存在产品参考图，旧的缺图提示已失效，我会重新调度出图 Agent。",
        payload={
            "stale_gate_id": pending.id,
            "reference_asset_ids": reference_ids,
            "next_agent": target_agent,
            "background": True,
        },
    )
    _start_agent_background(
        session_id=session_id,
        project_id=project.id,
        target_agent=target_agent,
        source_message="已有产品参考图，重新调度出图",
    )
    return True


def _assets_with_roles_or_kinds(project: ProjectRecord, roles: set[str], kinds: set[str]) -> list[Any]:
    return [
        asset
        for asset in project.assets
        if asset.kind in kinds or _asset_has_any_role(asset, roles)
    ]


def _has_product_dimensions(project: ProjectRecord) -> bool:
    blob = " ".join(
        [
            project.brief.raw_text or "",
            project.brief.core_product_definition or "",
            project.brief.category or "",
        ]
    )
    if re.search(r"\d+(?:\.\d+)?\s*(?:cm|mm|厘米|毫米|m)\s*[x×*乘]\s*\d+", blob, flags=re.IGNORECASE):
        return True
    if any(keyword in blob for keyword in ["尺寸", "长宽高", "配件", "盒型"]):
        return True
    return False


def _conversation_confirms_no_brand(session_id: str) -> bool:
    text = "\n".join(message.content for message in conversation_store.list_messages(session_id)[-12:])
    return any(marker in text.lower() for marker in ["无logo", "无 logo", "没有logo", "没有 logo", "无品牌", "无 vi", "无vi"])


def _ensure_assets_ready_or_schedule(*, session_id: str, project: ProjectRecord, target_agent: str) -> bool:
    assets = _assets_relevant_to_agent(project, target_agent)
    to_queue: list[str] = []
    waiting: list[str] = []
    for asset in assets:
        tool = _processing_tool_for_asset(asset)
        if not tool:
            continue
        processing = asset.metadata.get("processing") if isinstance(asset.metadata.get("processing"), dict) else {}
        status = str(processing.get("status") or "")
        version_ok = processing.get("parser_version") == parser_version_for_tool(tool)
        has_result = (
            isinstance(asset.metadata.get("document_parse"), dict)
            if tool == "document_parser"
            else isinstance(asset.metadata.get("image_analysis"), dict)
        )
        if status == "completed" and version_ok and has_result:
            continue
        if status in {"queued", "running"}:
            waiting.append(asset.filename)
            continue
        if status in {"failed", "cancelled"}:
            continue
        to_queue.append(asset.id)

    if not to_queue and not waiting:
        return True

    if to_queue:
        to_queue_set = set(to_queue)
        for asset in assets:
            if asset.id not in to_queue_set:
                continue
            tool = _processing_tool_for_asset(asset)
            if tool:
                mark_processing_queued(project.id, asset.id, tool=tool, reason=f"needed_by:{target_agent}")
        names = [asset.filename for asset in assets if asset.id in to_queue_set]
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content=f"我需要先解析 {len(names)} 个项目资产：{'、'.join(names[:4])}。解析完成后会自动继续当前 Agent。",
            payload={"queued_asset_ids": to_queue, "next_agent": target_agent},
        )
        background_task_queue.submit(
            kind="asset_processing",
            handler=_process_assets_and_continue,
            owner_id=project.owner_id,
            project_id=project.id,
            kwargs={
                "session_id": session_id,
                "project_id": project.id,
                "asset_ids": to_queue,
                "target_agent": target_agent,
            },
        )
    elif waiting:
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content=f"资料仍在解析中：{'、'.join(waiting[:4])}。完成后我会继续处理。",
            payload={"waiting_assets": waiting, "next_agent": target_agent},
        )
    return False


def _process_assets_and_continue(*, session_id: str, project_id: str, asset_ids: list[str], target_agent: str) -> None:
    for asset_id in asset_ids:
        try:
            project = project_store.get(project_id)
            asset = next((item for item in project.assets if item.id == asset_id), None)
            if not asset:
                continue
            conversation_store.add_message(
                session_id=session_id,
                role="tool",
                message_type="status",
                content=f"开始解析资料：{asset.filename}",
                payload={"asset_id": asset.id, "processing_status": "running"},
            )
            reports = enrich_project_assets(
                project_id=project_id,
                workflow_type=conversation_store.get_session(session_id).workflow_type,
                asset_ids=[asset_id],
            )
            _add_asset_intelligence_messages(session_id=session_id, reports=reports)
        except Exception as exc:
            conversation_store.add_message(
                session_id=session_id,
                role="tool",
                message_type="status",
                content=f"资料解析异常：{type(exc).__name__}: {exc}",
                payload={"asset_id": asset_id, "processing_status": "failed"},
            )
    try:
        project = project_store.get(project_id)
    except KeyError:
        return
    conversation_store.add_message(
        session_id=session_id,
        role="agent",
        message_type="status",
        content="资料解析队列已处理完毕，我将继续调度当前 Agent。",
        payload={"next_agent": target_agent},
    )
    _run_target_agent(session_id=session_id, project=project, target_agent=target_agent)


def _assets_relevant_to_agent(project: ProjectRecord, target_agent: str) -> list[Any]:
    if target_agent == "vi_understanding_agent":
        kinds = {"vi_document", "logo"}
        roles = {"vi_reference", "logo"}
    elif target_agent in {"usp_agent", "packaging_strategy_agent", "detail_page_strategy_agent", "strategy_agent", ""}:
        kinds = {
            "product_ppt",
            "product_pdf",
            "product_image",
            "transparent_product_image",
            "competitor_image",
            "competitor_packaging",
            "competitor_detail_page",
            "vi_document",
            "logo",
        }
        roles = {"product_intro", "product_image", "competitor_info", "vi_reference", "logo"}
    else:
        kinds = set()
        roles = set()
    return [
        asset
        for asset in project.assets
        if asset.kind in kinds or _asset_has_any_role(asset, roles)
    ]


def _asset_has_any_role(asset: Any, roles: set[str]) -> bool:
    bindings = asset.metadata.get("role_bindings") if isinstance(asset.metadata.get("role_bindings"), list) else []
    for binding in bindings:
        if isinstance(binding, dict) and binding.get("active", True) and binding.get("role") in roles:
            return True
    return False


def _processing_tool_for_asset(asset: Any) -> str:
    mime = (asset.mime_type or "").lower()
    suffix = str(asset.filename or "").lower()
    if mime.startswith("image/") or suffix.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")):
        return "image_understanding"
    if suffix.endswith((".ppt", ".pptx", ".pdf")) or "presentation" in mime or "pdf" in mime:
        return "document_parser"
    return ""


def handle_review_gate_action(
    *,
    session_id: str,
    gate_id: str,
    request: ConversationReviewActionRequest,
    owner_id: str | None = None,
) -> ConversationDetailResponse:
    session = conversation_store.get_session(session_id)
    _assert_session_owner(session, owner_id)
    project = project_store.get(session.project_id)
    _assert_project_owner(project, owner_id)
    gate = conversation_store.get_review_gate(session_id, gate_id)
    payload = request.edited_payload if request.action == "edit" and request.edited_payload is not None else gate.payload
    status_by_action = {
        "approve": "approved",
        "edit": "edited",
        "reject": "rejected",
        "request_more_info": "needs_more_info",
    }
    resolved = conversation_store.resolve_review_gate(
        session_id=session_id,
        gate_id=gate_id,
        status=status_by_action[request.action],
        payload=payload,
    )
    conversation_store.add_message(
        session_id=session_id,
        role="user",
        message_type="review_action",
        content=request.comment or _review_action_copy(request.action),
        payload={
            "gate_id": gate_id,
            "action": request.action,
            "reviewer": request.reviewer,
            "payload": payload,
        },
    )
    if request.action in {"approve", "edit"}:
        _commit_review_payload(session_id=session_id, gate=resolved, payload=payload)
        _continue_after_review(session_id=session_id, project=project, gate=resolved)
    elif request.action == "reject":
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content="已收到退回意见。我会带着当前资料和你的意见重新运行该节点。",
            payload={"rejected_gate": resolved.model_dump(mode="json")},
        )
        retry_agent = _agent_for_review_gate(resolved)
        if retry_agent:
            if _should_run_agent_in_background(retry_agent):
                conversation_store.add_message(
                    session_id=session_id,
                    role="agent",
                    message_type="status",
                    content="已重新调度出图 Agent，生成会在后台继续，审核卡会自动刷新。",
                    payload={"next_agent": retry_agent, "background": True},
                )
                _start_agent_background(
                    session_id=session_id,
                    project_id=project.id,
                    target_agent=retry_agent,
                    source_message=request.comment,
                )
            else:
                _run_target_agent(
                    session_id=session_id,
                    project=project_store.get(project.id),
                    target_agent=retry_agent,
                    source_message=request.comment,
                )
    return get_conversation_detail(session_id, owner_id=owner_id)


def _should_run_agent_in_background(target_agent: str) -> bool:
    if target_agent in {"packaging_designer_agent", "detail_designer_agent"}:
        return True
    default_async_agents = {
        "usp_agent",
        "vi_understanding_agent",
        "packaging_strategy_agent",
        "detail_page_strategy_agent",
        "strategy_agent",
        "packaging_image_prompt_agent",
    }
    return target_agent in default_async_agents and _is_default_agent_callable(target_agent)


def _is_default_agent_callable(target_agent: str) -> bool:
    callables = {
        "usp_agent": run_usp_agent,
        "vi_understanding_agent": run_vi_understanding_agent,
        "packaging_strategy_agent": run_packaging_strategy_agent,
        "detail_page_strategy_agent": run_detail_strategy_agent,
        "strategy_agent": run_packaging_strategy_agent,
        "packaging_image_prompt_agent": run_packaging_image_prompt_agent,
    }
    func = callables.get(target_agent)
    return bool(func and getattr(func, "__module__", "").startswith("ai_visual_agent.services.conversation_agents"))


def _dispatch_target_agent(
    *,
    session_id: str,
    project: ProjectRecord,
    target_agent: str,
    source_message: str = "",
    reason: str = "agent_dispatch",
) -> None:
    if _should_run_agent_in_background(target_agent):
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content=_agent_dispatch_status(target_agent),
            payload={"next_agent": target_agent, "background": True, "status": "running", "reason": reason},
        )
        _start_agent_background(
            session_id=session_id,
            project_id=project.id,
            target_agent=target_agent,
            source_message=source_message,
        )
        return
    _run_target_agent(
        session_id=session_id,
        project=project,
        target_agent=target_agent,
        source_message=source_message,
    )


def _agent_dispatch_status(target_agent: str) -> str:
    labels = {
        "usp_agent": "卖点提炼 Agent 已开始后台运行：正在结合项目描述、产品资料和竞品资料提炼核心卖点。",
        "vi_understanding_agent": "VI 理解 Agent 已开始后台运行：正在读取品牌色、LOGO 和视觉规范。",
        "packaging_strategy_agent": "包装策略 Agent 已开始后台运行：正在把卖点和 VI 转换成主图设计方案。",
        "detail_page_strategy_agent": "详情策略 Agent 已开始后台运行：正在规划详情页分屏内容。",
        "strategy_agent": "策略 Agent 已开始后台运行：正在生成视觉策略。",
        "packaging_image_prompt_agent": "主图提示词 Agent 已开始后台运行：正在把包装策略转换成可生图提示词。",
        "packaging_designer_agent": "出图 Agent 已开始后台运行：正在调用图像生成服务。",
        "detail_designer_agent": "详情出图 Agent 已开始后台运行：正在生成详情页视觉。",
    }
    return labels.get(target_agent, f"{target_agent} 已开始后台运行。")


def _agent_running_status(target_agent: str) -> str:
    labels = {
        "usp_agent": "卖点提炼 Agent 正在调用模型：整合项目描述、资料解析结果、竞品对比和用户期待。",
        "vi_understanding_agent": "VI 理解 Agent 正在调用模型：提取品牌色、LOGO、版式和禁用规则。",
        "packaging_strategy_agent": "包装策略 Agent 正在调用模型：把卖点动态化表达成主图构图、文案和标识方案。",
        "detail_page_strategy_agent": "详情策略 Agent 正在调用模型：规划详情页分屏内容。",
        "strategy_agent": "策略 Agent 正在调用模型：生成视觉策略。",
        "packaging_image_prompt_agent": "主图提示词 Agent 正在调用模型：把包装方案转换成图生图提示词。",
        "packaging_designer_agent": "出图 Agent 正在调用 Image API：根据参考图和主图提示词生成图像。",
        "detail_designer_agent": "详情出图 Agent 正在调用 Image API：生成详情页视觉。",
    }
    return labels.get(target_agent, f"{target_agent} 正在运行。")


def _start_agent_background(*, session_id: str, project_id: str, target_agent: str, source_message: str = "") -> None:
    project_owner = ""
    try:
        project_owner = project_store.get(project_id).owner_id
    except KeyError:
        project_owner = ""
    background_task_queue.submit(
        kind="agent_run",
        handler=_run_target_agent_background,
        owner_id=project_owner,
        project_id=project_id,
        kwargs={
            "session_id": session_id,
            "project_id": project_id,
            "target_agent": target_agent,
            "source_message": source_message,
        },
    )


def _run_target_agent_background(*, session_id: str, project_id: str, target_agent: str, source_message: str = "") -> None:
    try:
        raise_if_current_job_cancelled()
        project = project_store.get(project_id)
        _run_target_agent(
            session_id=session_id,
            project=project,
            target_agent=target_agent,
            source_message=source_message,
        )
        raise_if_current_job_cancelled()
    except TaskCancelledError:
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content="当前 Agent 任务已暂停，后台不会继续推进该节点。你可以修改要求后重新发送，或点击确认继续下一步。",
            payload={"next_agent": target_agent, "background": True, "cancelled": True},
        )
        raise
    except Exception as exc:
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content=f"后台调度 {target_agent} 失败：{type(exc).__name__}: {exc}",
            payload={"next_agent": target_agent, "background": True, "error": str(exc)},
        )


def _run_target_agent(*, session_id: str, project: ProjectRecord, target_agent: str, source_message: str = "") -> None:
    raise_if_current_job_cancelled()
    session = conversation_store.get_session(session_id)
    if not _check_dependencies_before_agent(session_id=session_id, project=project, target_agent=target_agent):
        return
    if target_agent in {
        "usp_agent",
        "vi_understanding_agent",
        "packaging_strategy_agent",
        "detail_page_strategy_agent",
        "strategy_agent",
        "",
    } and not _ensure_assets_ready_or_schedule(
        session_id=session_id,
        project=project,
        target_agent=target_agent or "packaging_strategy_agent",
    ):
        return
    conversation_store.add_message(
        session_id=session_id,
        role="agent",
        message_type="status",
        content=_agent_running_status(target_agent),
        payload={"next_agent": target_agent, "status": "running", "phase": "agent_running"},
    )
    if target_agent == "usp_agent":
        project = project_store.get(project.id)
        if _is_default_usp_agent():
            llm_result = run_usp_agent_result(project=project, source_message=source_message)
            candidates = llm_result.output
            diagnostics = _llm_diagnostics(llm_result.metadata())
        else:
            candidates = run_usp_agent(project=project, source_message=source_message)
            diagnostics = {"backend": "test", "model": "patched", "fallback_used": False, "status": "patched"}
        evidence_context = build_project_evidence_context(project)
        payload = candidates.model_dump(mode="json")
        payload["agent_diagnostics"] = diagnostics
        payload["evidence_summary"] = evidence_summary_for_review(evidence_context)
        gate = conversation_store.create_review_gate(
            session_id=session_id,
            gate_type="usp_review",
            title="请确认核心卖点和次要卖点",
            summary="确认后系统会把卖点写入有效记忆，并先进入 VI/LOGO 理解，再输出策略。",
            payload=payload,
            next_step_on_approve="vi_understanding_agent",
            created_by_agent="usp_agent",
        )
        conversation_store.update_session(session_id, current_stage="usp_review")
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="review_gate",
            content="我已提炼卖点，请确认是否进入下一步。",
            payload={"review_gate": gate.model_dump(mode="json")},
        )
        return

    confirmed_usps = session.confirmed_context.get("confirmed_usps") or {}
    if target_agent == "vi_understanding_agent":
        strategy_key = (
            "confirmed_detail_page_strategy"
            if session.workflow_type == "detail_page"
            else "confirmed_packaging_strategy"
        )
        confirmed_strategy = session.confirmed_context.get(strategy_key) or {}
        vi_assets = [
            asset
            for asset in project.assets
            if asset.kind in {"vi_document", "logo"} or _asset_has_any_role(asset, {"vi_reference", "logo"})
        ]
        profile = run_vi_understanding_agent(
            project=project,
            confirmed_strategy=confirmed_strategy,
            confirmed_usps=confirmed_usps,
            workflow_type=session.workflow_type,
            revision_request=source_message,
        )
        next_agent = "detail_page_strategy_agent" if session.workflow_type == "detail_page" else "packaging_strategy_agent"
        gate = conversation_store.create_review_gate(
            session_id=session_id,
            gate_type="vi_review",
            title="\u8bf7\u5148\u4e0a\u4f20 VI/LOGO \u6216\u786e\u8ba4\u65e0\u54c1\u724c\u89c4\u8303" if not vi_assets else "\u8bf7\u786e\u8ba4 VI \u89c6\u89c9\u89c4\u8303\u7406\u89e3",
            summary=(
                "\u672a\u68c0\u6d4b\u5230 VI \u6587\u6863\u6216 LOGO\u3002\u8bf7\u5728\u8f93\u5165\u533a\u4e0a\u4f20 VI/LOGO\uff1b\u5982\u786e\u8ba4\u672c\u9879\u76ee\u65e0\u54c1\u724c\u89c4\u8303\uff0c\u53ef\u76f4\u63a5\u786e\u8ba4\u7ee7\u7eed\uff0c\u7cfb\u7edf\u4e0d\u4f1a\u865a\u6784 LOGO\u3002"
                if not vi_assets
                else "\u786e\u8ba4\u540e\u5c06\u628a VI \u89c4\u8303\u5199\u5165\u9879\u76ee\u8bb0\u5fc6\uff0c\u5e76\u81ea\u52a8\u8c03\u5ea6\u7b56\u7565 Agent\u3002"
            ),
            payload=profile.model_dump(mode="json"),
            next_step_on_approve=next_agent,
            created_by_agent="vi_understanding_agent",
        )
        conversation_store.update_session(session_id, current_stage="vi_review")
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="review_gate",
            content="\u6211\u5df2\u7406\u89e3 VI \u89c6\u89c9\u89c4\u8303\uff0c\u8bf7\u786e\u8ba4\u662f\u5426\u7528\u4e8e\u540e\u7eed\u7b56\u7565\u548c\u51fa\u56fe\u3002",
            payload={"review_gate": gate.model_dump(mode="json")},
        )
        return

    if target_agent == "packaging_image_prompt_agent":
        confirmed_strategy = session.confirmed_context.get("confirmed_packaging_strategy") or {}
        confirmed_vi_profile = session.confirmed_context.get("confirmed_vi_profile") or {}
        prompt_draft = run_packaging_image_prompt_agent(
            project=project,
            confirmed_usps=confirmed_usps,
            confirmed_vi_profile=confirmed_vi_profile,
            packaging_strategy=confirmed_strategy,
            revision_request=source_message,
        )
        gate = conversation_store.create_review_gate(
            session_id=session_id,
            gate_type="image_prompt_review",
            title="请确认包装主图生图提示词",
            summary="确认后将把这段提示词写入项目记忆，并只调用出图 Agent 生成包装主图/正面图。",
            payload=prompt_draft.model_dump(mode="json"),
            next_step_on_approve="packaging_designer_agent",
            created_by_agent="packaging_image_prompt_agent",
        )
        conversation_store.update_session(session_id, current_stage="image_prompt_review")
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="review_gate",
            content="我已把包装策略转换成主图生图提示词，请先确认提示词再出图。",
            payload={"review_gate": gate.model_dump(mode="json")},
        )
        return

    if target_agent in {"packaging_designer_agent", "detail_designer_agent"}:
        workflow_type = "detail_page" if target_agent == "detail_designer_agent" else "packaging"
        strategy_key = "confirmed_detail_page_strategy" if workflow_type == "detail_page" else "confirmed_packaging_strategy"
        confirmed_strategy = session.confirmed_context.get(strategy_key) or {}
        vi_profile = session.confirmed_context.get("confirmed_vi_profile") or {}
        image_prompt = session.confirmed_context.get("confirmed_image_prompt") or {}
        if workflow_type == "packaging" and not image_prompt:
            _run_target_agent(
                session_id=session_id,
                project=project,
                target_agent="packaging_image_prompt_agent",
                source_message=source_message,
            )
            return
        reference_prompt_context = _image_reference_prompt_context(
            confirmed_strategy=confirmed_strategy,
            confirmed_image_prompt=image_prompt if workflow_type == "packaging" else None,
            revision_request=source_message,
        )
        planned_reference_ids = _image_generation_reference_asset_ids(
            project,
            vi_profile,
            prompt_context=reference_prompt_context,
        )
        product_reference_ids = set(_product_reference_asset_ids(project, prompt_context=reference_prompt_context))
        reference_assets = [asset for asset in project.assets if asset.id in product_reference_ids]
        if not reference_assets:
            gate = conversation_store.create_review_gate(
                session_id=session_id,
                gate_type="final_design_review",
                title="\u51fa\u56fe\u524d\u9700\u5148\u4e0a\u4f20\u4ea7\u54c1\u53c2\u8003\u56fe",
                summary="\u5305\u88c5\u51fa\u56fe\u5fc5\u987b\u4f7f\u7528\u4ea7\u54c1\u53c2\u8003\u56fe\u8fdb\u884c\u56fe\u751f\u56fe\u3002\u8bf7\u5728\u8f93\u5165\u533a\u4e0a\u4f20\u4ea7\u54c1\u56fe\uff0c\u4e0a\u4f20\u540e\u7cfb\u7edf\u4f1a\u91cd\u65b0\u8c03\u5ea6\u51fa\u56fe Agent\u3002",
                payload={
                    "generation_blocked": True,
                    "reason": "\u7f3a\u5c11\u4ea7\u54c1\u53c2\u8003\u56fe",
                    "required_assets": ["product_image"],
                    "confirmed_strategy": confirmed_strategy,
                    "vi_profile": vi_profile,
                },
                next_step_on_approve=target_agent,
                created_by_agent=target_agent,
            )
            conversation_store.update_session(session_id, current_stage="final_design_review")
            conversation_store.add_message(
                session_id=session_id,
                role="agent",
                message_type="review_gate",
                content="\u51fa\u56fe Agent \u9700\u8981\u4ea7\u54c1\u53c2\u8003\u56fe\u624d\u80fd\u7ee7\u7eed\uff0c\u8bf7\u4e0a\u4f20\u4ea7\u54c1\u56fe\u3002",
                payload={"review_gate": gate.model_dump(mode="json")},
            )
            return
        gate = conversation_store.create_review_gate(
            session_id=session_id,
            gate_type="final_design_review",
            title="\u51fa\u56fe Agent \u6b63\u5728\u751f\u6210\u4e3b\u56fe" if workflow_type == "packaging" else "\u51fa\u56fe Agent \u6b63\u5728\u9010\u9762\u751f\u6210",
            summary=(
                "\u786e\u8ba4\u8fc7\u7684\u4e3b\u56fe\u63d0\u793a\u8bcd\u5c06\u88ab\u76f4\u63a5\u7528\u4e8e\u56fe\u751f\u56fe\uff0c\u672c\u6b21\u53ea\u751f\u6210\u5305\u88c5\u4e3b\u56fe/\u6b63\u9762\u56fe\u3002"
                if workflow_type == "packaging"
                else "\u6bcf\u5b8c\u6210\u4e00\u4e2a\u89c6\u56fe\u90fd\u4f1a\u7acb\u5373\u5199\u5165\u5ba1\u6838\u5361\uff0c\u5373\u4f7f\u540e\u7eed\u89c6\u56fe\u56e0\u989d\u5ea6\u6216 API \u5931\u8d25\uff0c\u5df2\u751f\u6210\u56fe\u4e5f\u4f1a\u4fdd\u7559\u3002"
            ),
            payload=_design_generation_payload(
                items=[],
                revision_round=0,
                total=_expected_design_output_count(
                    workflow_type,
                    confirmed_strategy,
                    image_prompt if workflow_type == "packaging" else None,
                ),
                status="running",
                errors=[],
                reference_asset_ids=planned_reference_ids,
            ),
            next_step_on_approve="archive",
            created_by_agent=target_agent,
        )
        conversation_store.update_session(session_id, current_stage="final_design_review")
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="review_gate",
            content=(
                "\u51fa\u56fe Agent \u5df2\u5f00\u59cb\u751f\u6210\u5305\u88c5\u4e3b\u56fe\uff0c\u5b8c\u6210\u540e\u4f1a\u5f39\u51fa\u8bbe\u8ba1\u56fe\u786e\u8ba4\u5361\u3002"
                if workflow_type == "packaging"
                else "\u51fa\u56fe Agent \u5df2\u5f00\u59cb\u9010\u9762\u751f\u6210\uff0c\u89c6\u56fe\u751f\u6210\u7ed3\u675f\u540e\u4f1a\u5f39\u51fa\u8bbe\u8ba1\u56fe\u786e\u8ba4\u5361\u3002"
            ),
            payload={"review_gate": gate.model_dump(mode="json")},
        )

        generation_errors: list[dict[str, Any]] = []
        expected_total = _expected_design_output_count(
            workflow_type,
            confirmed_strategy,
            image_prompt if workflow_type == "packaging" else None,
        )

        def publish_progress(*, items: list[Any], status: str, errors: list[dict[str, Any]]) -> None:
            completed = len(items)
            title = _design_gate_title(workflow_type=workflow_type, status=status, completed=completed, total=expected_total)
            summary = _design_gate_summary(status=status, completed=completed, total=expected_total, errors=errors)
            conversation_store.update_review_gate_payload(
                session_id=session_id,
                gate_id=gate.id,
                title=title,
                summary=summary,
                payload=_design_generation_payload(
                    items=items,
                    revision_round=0,
                    total=expected_total,
                    status=status,
                    errors=errors,
                    reference_asset_ids=planned_reference_ids,
                ),
            )

        def on_item_generated(item: Any, items: list[Any], total: int) -> None:
            status = "completed" if len(items) >= total else "running"
            publish_progress(items=items, status=status, errors=generation_errors)
            conversation_store.add_message(
                session_id=session_id,
                role="agent",
                message_type="status",
                content=f"\u5df2\u751f\u6210 {item.name} \u89c6\u56fe\uff08{len(items)}/{total}\uff09\u3002",
                payload={"generated_item": item.model_dump(mode="json"), "review_gate_id": gate.id},
            )

        def on_generation_error(job: Any, error: dict[str, Any], items: list[Any], total: int) -> None:
            generation_errors.append(error)
            status = "partial_failed" if items else "failed"
            publish_progress(items=items, status=status, errors=generation_errors)
            conversation_store.add_message(
                session_id=session_id,
                role="agent",
                message_type="status",
                content=f"{job.name} \u89c6\u56fe\u751f\u6210\u5931\u8d25\uff0c\u5df2\u4fdd\u7559\u524d\u9762\u5b8c\u6210\u7684\u8f93\u51fa\u3002",
                payload={"generation_error": error, "review_gate_id": gate.id},
            )

        try:
            raise_if_current_job_cancelled()
            output = run_design_agent(
                project=project,
                workflow_type=workflow_type,
                confirmed_strategy=confirmed_strategy,
                confirmed_vi_profile=vi_profile,
                confirmed_image_prompt=image_prompt if workflow_type == "packaging" else None,
                revision_request=source_message,
                reference_prompt_context=reference_prompt_context,
                return_partial_on_error=True,
                on_item_generated=on_item_generated,
                on_generation_error=on_generation_error,
            )
            raise_if_current_job_cancelled()
        except TaskCancelledError:
            generation_errors.append({"name": "cancelled", "error": "cancelled_by_user"})
            publish_progress(items=[], status="cancelled", errors=generation_errors)
            conversation_store.add_message(
                session_id=session_id,
                role="agent",
                message_type="status",
                content="出图任务已暂停，已停止后续生成与写入。你可以调整要求后重新发送。",
                payload={"review_gate_id": gate.id, "generation_cancelled": True},
            )
            raise
        except Exception as exc:
            generation_errors.append({"name": "setup", "error": str(exc)})
            publish_progress(items=[], status="failed", errors=generation_errors)
            return

        final_status = "partial_failed" if generation_errors else "completed"
        publish_progress(items=list(output.items), status=final_status, errors=generation_errors)
        return

    if target_agent == "detail_page_strategy_agent" or (
        target_agent == "strategy_agent" and session.workflow_type == "detail_page"
    ):
        confirmed_vi_profile = session.confirmed_context.get("confirmed_vi_profile") or {}
        strategy = run_detail_strategy_agent(
            project=project,
            confirmed_usps=confirmed_usps,
            confirmed_vi_profile=confirmed_vi_profile,
            revision_request=source_message,
        )
        gate = conversation_store.create_review_gate(
            session_id=session_id,
            gate_type="detail_strategy_review",
            title="请确认详情页五屏策略",
            summary="确认后可直接进入详情页出图。",
            payload=strategy.model_dump(mode="json"),
            next_step_on_approve="detail_designer_agent",
            created_by_agent="detail_page_strategy_agent",
        )
        conversation_store.update_session(session_id, current_stage="detail_strategy_review")
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="review_gate",
            content="我已输出详情页策略，请确认。",
            payload={"review_gate": gate.model_dump(mode="json")},
        )
        return

    if target_agent in {"packaging_strategy_agent", "strategy_agent", ""}:
        confirmed_vi_profile = session.confirmed_context.get("confirmed_vi_profile") or {}
        strategy = run_packaging_strategy_agent(
            project=project,
            confirmed_usps=confirmed_usps,
            confirmed_vi_profile=confirmed_vi_profile,
            revision_request=source_message,
        )
        gate = conversation_store.create_review_gate(
            session_id=session_id,
            gate_type="packaging_strategy_review",
            title="请确认包装四面策略",
            summary="确认后会先进入主图生图提示词确认，再调用出图 Agent 生成包装主图。",
            payload=strategy.model_dump(mode="json"),
            next_step_on_approve="packaging_image_prompt_agent",
            created_by_agent="packaging_strategy_agent",
        )
        conversation_store.update_session(session_id, current_stage="packaging_strategy_review")
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="review_gate",
            content="我已输出包装策略，请确认。",
            payload={"review_gate": gate.model_dump(mode="json")},
        )


def _agent_for_review_gate(gate: ConversationReviewGate) -> str:
    if gate.type == "final_design_review":
        if gate.created_by_agent in {"packaging_designer_agent", "detail_designer_agent"}:
            return gate.created_by_agent
        payload = gate.payload if isinstance(gate.payload, dict) else {}
        generated_outputs = payload.get("generated_outputs") if isinstance(payload.get("generated_outputs"), dict) else {}
        items = generated_outputs.get("items") if isinstance(generated_outputs.get("items"), list) else []
        if items and any(str(item.get("name") or "").startswith("screen_") for item in items if isinstance(item, dict)):
            return "detail_designer_agent"
        return "packaging_designer_agent"
    if gate.created_by_agent:
        return gate.created_by_agent
    return {
        "usp_review": "usp_agent",
        "vi_review": "vi_understanding_agent",
        "packaging_strategy_review": "packaging_strategy_agent",
        "image_prompt_review": "packaging_image_prompt_agent",
        "detail_strategy_review": "detail_page_strategy_agent",
        "final_design_review": gate.next_step_on_approve or "",
    }.get(gate.type, "")


def _is_default_usp_agent() -> bool:
    return getattr(run_usp_agent, "__module__", "") == "ai_visual_agent.services.conversation_agents"


def _llm_diagnostics(metadata: dict[str, Any]) -> dict[str, Any]:
    fallback_used = bool(metadata.get("fallback_used"))
    return {
        "backend": metadata.get("backend"),
        "model": metadata.get("model"),
        "prompt_name": metadata.get("prompt_name"),
        "prompt_version": metadata.get("prompt_version"),
        "prompt_hash": metadata.get("prompt_hash"),
        "fallback_used": fallback_used,
        "error": metadata.get("error"),
        "status": "fallback" if fallback_used else "model_returned",
    }


def _expected_design_output_count(
    workflow_type: str,
    strategy: dict[str, Any],
    image_prompt: dict[str, Any] | None = None,
) -> int:
    if workflow_type == "detail_page":
        screens = strategy.get("screens") if isinstance(strategy, dict) else []
        return max(1, len(screens) if isinstance(screens, list) else 0)
    if image_prompt:
        return 1
    return 4


def _design_generation_payload(
    *,
    items: list[Any],
    revision_round: int,
    total: int,
    status: str,
    errors: list[dict[str, Any]],
    reference_asset_ids: list[str] | None = None,
) -> dict[str, Any]:
    serialized_items = [
        item.model_dump(mode="json") if hasattr(item, "model_dump") else item
        for item in items
    ]
    actual_reference_ids = _reference_ids_from_generated_items(serialized_items, "actual_reference_asset_ids")
    item_reference_ids = _reference_ids_from_generated_items(serialized_items, "reference_asset_ids")
    return {
        "generated_outputs": {
            "items": serialized_items,
            "revision_round": revision_round,
        },
        "generation_status": status,
        "generation_progress": {
            "completed": len(serialized_items),
            "total": total,
        },
        "generation_errors": errors,
        "reference_asset_ids": item_reference_ids or reference_asset_ids or [],
        "actual_reference_asset_ids": actual_reference_ids,
    }


def _reference_ids_from_generated_items(items: list[dict[str, Any]], key: str) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for item in items:
        layout_spec = item.get("layout_spec") if isinstance(item.get("layout_spec"), dict) else {}
        raw_ids = layout_spec.get(key) if isinstance(layout_spec.get(key), list) else []
        for raw_id in raw_ids:
            asset_id = str(raw_id or "")
            if asset_id and asset_id not in seen:
                ids.append(asset_id)
                seen.add(asset_id)
    return ids


def _design_gate_title(*, workflow_type: str, status: str, completed: int, total: int) -> str:
    label = "详情页设计图" if workflow_type == "detail_page" else "包装设计图"
    if status == "completed":
        return f"请确认{label}（已完成 {completed}/{total}）"
    if status == "partial_failed":
        return f"{label}部分完成（{completed}/{total}）"
    if status == "failed":
        return f"{label}生成失败（{completed}/{total}）"
    if status == "cancelled":
        return f"{label}已暂停（{completed}/{total}）"
    return f"{label}生成中（{completed}/{total}）"


def _design_gate_summary(*, status: str, completed: int, total: int, errors: list[dict[str, Any]]) -> str:
    if status == "completed":
        if total == 1:
            return "包装主图已生成完成。确认后将把生成图写入项目记忆；如需修改可退回重做。"
        return "所有视图已生成完成。确认后将把生成图写入项目记忆；如需修改可退回重做。"
    if status == "partial_failed":
        latest_error = errors[-1].get("error", "") if errors else ""
        return f"已保留成功生成的 {completed}/{total} 个视图，后续视图生成失败。失败原因：{latest_error}"
    if status == "failed":
        latest_error = errors[-1].get("error", "") if errors else ""
        return f"尚未生成成功视图。失败原因：{latest_error}"
    if status == "cancelled":
        return "当前生成任务已由用户暂停，后续生成与结果写入已停止。"
    if total == 1:
        return "出图 Agent 正在生成包装主图，完成后会写入当前审核卡。"
    return "出图 Agent 正在逐面生成，已完成的视图会先写入当前审核卡。"


def _commit_review_payload(*, session_id: str, gate: ConversationReviewGate, payload: dict[str, Any]) -> None:
    key_by_type = {
        "usp_review": "confirmed_usps",
        "packaging_strategy_review": "confirmed_packaging_strategy",
        "image_prompt_review": "confirmed_image_prompt",
        "detail_strategy_review": "confirmed_detail_page_strategy",
        "vi_review": "confirmed_vi_profile",
        "final_design_review": "confirmed_outputs",
    }
    key = key_by_type.get(gate.type)
    if key:
        conversation_store.update_session(session_id, confirmed_context_patch={key: payload})
    if gate.type in {"packaging_strategy_review", "detail_strategy_review"}:
        product_name = _clean_product_name(str(payload.get("product_name") or ""))
        if product_name:
            session = conversation_store.get_session(session_id)
            candidate_title = _title_from_text(product_name)
            if _should_update_session_title(session.title, candidate_title):
                conversation_store.update_session(session_id, title=candidate_title)


def _continue_after_review(*, session_id: str, project: ProjectRecord, gate: ConversationReviewGate) -> None:
    if gate.type == "usp_review":
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content="卖点已确认，我将先调度 VI 理解 Agent，确认品牌/LOGO 约束后再输出策略。",
            payload={"confirmed_key": "confirmed_usps", "next_agent": "vi_understanding_agent"},
        )
        _run_target_agent(
            session_id=session_id,
            project=project,
            target_agent="vi_understanding_agent",
        )
        return
    if gate.type in {"packaging_strategy_review", "detail_strategy_review"}:
        next_agent = (
            "detail_designer_agent"
            if gate.type == "detail_strategy_review"
            else "packaging_image_prompt_agent"
        )
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content=(
                "\u5305\u88c5\u7b56\u7565\u5df2\u786e\u8ba4\uff0c\u6211\u5c06\u5148\u628a\u7b56\u7565\u8f6c\u6210\u53ef\u5ba1\u6838\u7684\u4e3b\u56fe\u751f\u56fe\u63d0\u793a\u8bcd\u3002"
                if gate.type == "packaging_strategy_review"
                else "\u7b56\u7565\u5df2\u786e\u8ba4\uff0c\u6211\u5c06\u7ee7\u7eed\u8c03\u5ea6\u51fa\u56fe Agent\u3002"
            ),
            payload={"confirmed_gate_type": gate.type, "next_agent": next_agent},
        )
        _run_target_agent(
            session_id=session_id,
            project=project,
            target_agent=next_agent,
        )
        return
    if gate.type == "image_prompt_review":
        next_agent = "packaging_designer_agent"
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content="\u4e3b\u56fe\u63d0\u793a\u8bcd\u5df2\u786e\u8ba4\uff0c\u6211\u5c06\u8c03\u5ea6\u5305\u88c5\u51fa\u56fe Agent \u751f\u6210\u4e3b\u56fe\u3002",
            payload={"confirmed_gate_type": gate.type, "next_agent": next_agent, "background": True},
        )
        _start_agent_background(
            session_id=session_id,
            project_id=project.id,
            target_agent=next_agent,
        )
        return
    if gate.type == "vi_review":
        next_agent = gate.next_step_on_approve or (
            "detail_page_strategy_agent"
            if conversation_store.get_session(session_id).workflow_type == "detail_page"
            else "packaging_strategy_agent"
        )
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content="\u89c6\u89c9\u89c4\u8303\u5df2\u786e\u8ba4\uff0c\u6211\u5c06\u57fa\u4e8e\u5356\u70b9\u548c VI \u7ee7\u7eed\u8c03\u5ea6\u7b56\u7565 Agent\u3002",
            payload={"confirmed_gate_type": gate.type, "next_agent": next_agent},
        )
        _run_target_agent(
            session_id=session_id,
            project=project,
            target_agent=next_agent,
        )
        return
    if gate.type == "final_design_review":
        conversation_store.update_session(session_id, current_stage="completed")
        conversation_store.add_message(
            session_id=session_id,
            role="agent",
            message_type="status",
            content="\u8bbe\u8ba1\u56fe\u5df2\u786e\u8ba4\uff0c\u5f53\u524d\u4efb\u52a1\u5df2\u5b8c\u6210\u3002",
            payload={"confirmed_gate_type": gate.type},
        )
        return
    conversation_store.add_message(
        session_id=session_id,
        role="agent",
        message_type="status",
        content="该节点已确认，后续会进入下一阶段能力接入。",
        payload={"confirmed_gate_type": gate.type},
    )


def _brief_from_text(text: str) -> ProjectBrief:
    product = _extract_product_phrase(text)
    value_proposition = _extract_after(
        text,
        ["差异化价值主张是", "核心价值主张是", "价值主张是", "我们的核心价值主张是"],
        default="",
        stop_markers=["，我上传", "。", "\n"],
    )
    expectations = _extract_list_after(text, ["用户关注指标是", "用户的品类期待是", "用户关注", "品类期待是"])
    if not expectations and value_proposition:
        expectations = _split_cn_list(value_proposition)
    return ProjectBrief(
        category=_extract_after(text, ["品类是", "品类为"], default=product),
        target_user=_extract_after(
            text,
            ["目标用户是", "目标人群是", "市场群体是", "人群是", "用户是"],
            default="",
        ),
        user_expectations=expectations,
        user_metrics=expectations,
        value_proposition=value_proposition,
        core_product_definition=_extract_after(text, ["核心产品定义是"], default=product or _title_from_text(text)),
        raw_text=text,
    )


def _update_project_brief_from_message(project: ProjectRecord, message: str) -> ProjectBrief | None:
    if not message.strip():
        return None
    current = project.brief
    parsed = _brief_from_text(message)
    merged = ProjectBrief(
        category=_pick_brief_value(current.category, parsed.category),
        target_user=_pick_brief_value(current.target_user, parsed.target_user),
        user_expectations=current.user_expectations or parsed.user_expectations,
        user_metrics=current.user_metrics or parsed.user_metrics or parsed.user_expectations,
        value_proposition=_pick_brief_value(current.value_proposition, parsed.value_proposition),
        core_product_definition=_pick_brief_value(current.core_product_definition, parsed.core_product_definition),
        raw_text="\n".join(part for part in [current.raw_text, message] if part).strip(),
    )
    project_store.update(project.id, ProjectUpdateRequest(brief=merged))
    return merged


def _apply_basic_workflow_inference(session_id: str, message: str):
    if "详情" in message:
        return conversation_store.update_session(session_id, workflow_type="detail_page")
    if "包装" in message:
        return conversation_store.update_session(session_id, workflow_type="packaging")
    return conversation_store.get_session(session_id)


def _title_from_text(text: str) -> str:
    clean = _clean_product_name(text) or " ".join(text.split())
    return clean[:28] or "新项目对话"


def _title_from_brief(brief: ProjectBrief, *, fallback: str = "") -> str:
    for value in [
        brief.core_product_definition,
        _extract_product_phrase(brief.raw_text),
        brief.category,
        brief.value_proposition,
        fallback,
    ]:
        clean = _clean_product_name(value)
        if clean and not _is_placeholder(clean):
            return _title_from_text(clean)
    return "新项目对话"


def _title_from_project(project: ProjectRecord) -> str:
    brief = project.brief
    for value in [
        brief.core_product_definition,
        _extract_product_phrase(brief.raw_text),
        brief.category,
        brief.value_proposition,
        brief.raw_text,
    ]:
        clean = _clean_product_name(value)
        if clean and not _is_placeholder(clean):
            return _title_from_text(clean)
    return f"{'详情提案' if project.workflow_type == 'detail_page' else '包装概念'}项目"


def _extract_product_phrase(text: str) -> str:
    patterns = [
        r"(?:make|create|design)\s+(?:a\s+)?(?:packaging|packaging concept|package)\s+(?:for|of)\s+(?P<value>.+?)(?:,|\.|\btarget users\b|\busers\b|\bplease\b|$)",
        r"(?:我想|想)?做一款(?P<value>.+?)(?:的包装提案|包装提案|的包装|包装|详情页|详情提案|项目|，|。)",
        r"(?:我想|想)?做一个(?P<value>.+?)(?:的包装提案|包装提案|的包装|包装|详情页|详情提案|项目|，|。)",
        r"这是一个(?P<value>.+?)(?:品类的项目|品类项目|项目|，|。)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group("value").strip(" _：:，,。")
    return ""


def _clean_product_name(value: str) -> str:
    clean = " ".join(str(value or "").split()).strip(" _：:，,。")
    if not clean:
        return ""
    clean = re.sub(
        r"^(?:i\s+want\s+to\s+|please\s+)?(?:make|create|design)\s+(?:a\s+)?(?:packaging|packaging concept|package)\s+(?:for|of)\s+",
        "",
        clean,
        flags=re.IGNORECASE,
    ).strip(" _：:，,。")
    clean = re.split(r"\s*(?:请|我已|我上传|上传|@)", clean, maxsplit=1)[0].strip(" _：:，,。")
    clean = re.sub(r"^(?:这是)?(?:一个|一款|一套)", "", clean).strip(" _：:，,。")
    clean = re.sub(r"(?:品类的项目|品类项目|包装概念方案|包装提案|详情提案|详情页|包装|项目)$", "", clean).strip(" _：:，,。")
    possessive_match = re.search(r"^(?P<modifier>.+?)的(?P<name>[^的，。,；;]{2,24})$", clean)
    if possessive_match:
        modifier = possessive_match.group("modifier")
        name = possessive_match.group("name").strip(" _：:，,。\"“”")
        if any(marker in modifier for marker in ["“", "”", "\"", "、", "，", ","]) or len(modifier) >= 8:
            clean = name
    return clean.strip(" _：:，,。\"“”")[:40]


def _should_update_session_title(current: str, candidate: str) -> bool:
    candidate = _title_from_text(candidate)
    current_clean = (current or "").strip()
    if not candidate or _is_placeholder(candidate):
        return False
    if _is_placeholder(current_clean):
        return True
    if current_clean == candidate:
        return False
    noisy_current = _clean_product_name(current_clean)
    if noisy_current != current_clean and noisy_current == candidate:
        return True
    if len(current_clean) > 20 and len(candidate) < len(current_clean):
        return True
    return False


def _pick_brief_value(current: str, parsed: str) -> str:
    if parsed and _is_placeholder(current):
        return parsed
    return current or parsed


def _is_placeholder(value: str) -> bool:
    clean = (value or "").strip()
    return clean in {"", "新项目对话", "未命名对话", "未命名项目", "包装概念项目", "详情提案项目"}


def _extract_after(
    text: str,
    markers: list[str],
    default: str = "",
    stop_markers: list[str] | None = None,
) -> str:
    for marker in markers:
        if marker in text:
            value = text.split(marker, 1)[1]
            for stop in stop_markers or ["，", ",", "。", "\n", "我们的", "用户", "我上传"]:
                value = value.split(stop, 1)[0]
            return value.strip("_ ：:，,。")
    return default


def _extract_list_after(text: str, markers: list[str]) -> list[str]:
    value = _extract_after(text, markers)
    if not value:
        return []
    return _split_cn_list(value)


def _split_cn_list(value: str) -> list[str]:
    return [
        item.strip()
        for item in value.replace("、", ",").replace("，", ",").replace("；", ",").replace(";", ",").split(",")
        if item.strip()
    ]


register_background_handler("asset_processing", _process_assets_and_continue)
register_background_handler("agent_run", _run_target_agent_background)


def _review_action_copy(action: str) -> str:
    return {
        "approve": "确认无误，进入下一步",
        "edit": "已人工修改并确认",
        "reject": "退回重做",
        "request_more_info": "需要补充资料",
    }.get(action, "已处理审核卡片")
