from io import BytesIO
from datetime import UTC, datetime
import json
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import (
    AssetKind,
    AssetRef,
    AssetUpdateRequest,
    AgentChatMessage,
    AgentChatRequest,
    AgentChatResponse,
    AuthUser,
    AuditRecord,
    AuditRecordType,
    ConversationBatchDeleteRequest,
    ConversationBatchDeleteResult,
    ConversationCreateRequest,
    ConversationDetailResponse,
    ConversationMessageCreateRequest,
    ConversationReviewActionRequest,
    GoldenFixtureSummary,
    GoldenRunResult,
    HumanReviewInput,
    ImageAssetAnalysis,
    IntegrationProbeRequest,
    IntegrationProbeResult,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseEntry,
    KnowledgeBaseUpdateRequest,
    KnowledgeDomain,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    KnowledgeStatus,
    KnowledgeWorkflowType,
    MemorySearchRequest,
    MemorySearchResult,
    MemoryUpsertRequest,
    PromptVersion,
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectKnowledgePreviewResponse,
    ProjectRecord,
    ProjectUpdateRequest,
    SegmentationResult,
    WorkflowResult,
)
from ai_visual_agent.api.dependencies import require_current_user
from ai_visual_agent.services.audit_store import audit_store
from ai_visual_agent.services.asset_memory import register_asset_memory
from ai_visual_agent.services.asset_processing import (
    cancel_asset_processing,
    reuse_completed_cache_for_asset,
    uploaded_processing_patch,
)
from ai_visual_agent.services.conversation_service import (
    create_conversation,
    delete_conversation,
    delete_conversations_batch,
    delete_project_workspace,
    get_conversation_detail,
    handle_review_gate_action,
    handle_user_message,
    list_conversations,
    upload_conversation_asset,
)
from ai_visual_agent.services.golden_regression import list_golden_fixtures, run_golden_fixture
from ai_visual_agent.services.image_analysis import analyze_image_asset
from ai_visual_agent.services.integration_probe import run_integration_probe
from ai_visual_agent.services.knowledge_store import build_project_knowledge_context, knowledge_store, search_knowledge
from ai_visual_agent.services.orchestrator_agent import list_agent_messages, run_orchestrator_turn
from ai_visual_agent.services.project_detail import build_project_detail
from ai_visual_agent.services.prompt_registry import get_prompt_registry
from ai_visual_agent.services.project_store import project_store
from ai_visual_agent.services.rate_limiter import (
    RateLimitExceeded,
    RateLimiterUnavailable,
    enforce_rate_limit,
)
from ai_visual_agent.services.segmentation import segment_image_asset
from ai_visual_agent.services.storage import asset_storage
from ai_visual_agent.services.memory_store import get_memory_store
from ai_visual_agent.services.task_queue import background_task_queue
from ai_visual_agent.services.workflow_engine import workflow_engine

router = APIRouter(prefix="/api", tags=["workflow"], dependencies=[Depends(require_current_user)])


def _project_for_user(project_id: str, user: AuthUser) -> ProjectRecord:
    project = project_store.get(project_id)
    if project.owner_id and project.owner_id.strip().lower() != user.id.strip().lower():
        raise HTTPException(status_code=403, detail="You do not have access to this project.")
    return project


def _rate_limit_or_429(*, user: AuthUser, scope: str, limit: int) -> None:
    try:
        enforce_rate_limit(scope=scope, identity=user.id, limit=limit)
    except RateLimitExceeded as exc:
        retry_after = str(exc.decision.retry_after_seconds)
        raise HTTPException(
            status_code=429,
            detail=exc.decision.to_detail(),
            headers={"Retry-After": retry_after},
        ) from exc
    except RateLimiterUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Rate limit backend is unavailable.",
                "scope": scope,
                "backend": exc.backend,
            },
        ) from exc


@router.post("/conversations", response_model=ConversationDetailResponse)
def create_conversation_session(
    request: ConversationCreateRequest,
    user: AuthUser = Depends(require_current_user),
) -> ConversationDetailResponse:
    settings = get_settings()
    _rate_limit_or_429(user=user, scope="conversation_create", limit=settings.rate_limit_default_per_minute)
    return create_conversation(request, owner_id=user.id)


@router.get("/conversations", response_model=list[ConversationDetailResponse])
def list_conversation_sessions(user: AuthUser = Depends(require_current_user)) -> list[ConversationDetailResponse]:
    return list_conversations(owner_id=user.id)


@router.get("/conversations/{session_id}", response_model=ConversationDetailResponse)
def get_conversation_session(
    session_id: str,
    user: AuthUser = Depends(require_current_user),
) -> ConversationDetailResponse:
    try:
        return get_conversation_detail(session_id, owner_id=user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.delete("/conversations/{session_id}", status_code=204)
def delete_conversation_session(
    session_id: str,
    user: AuthUser = Depends(require_current_user),
) -> Response:
    try:
        delete_conversation(session_id, owner_id=user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/conversations/batch-delete", response_model=ConversationBatchDeleteResult)
def batch_delete_conversation_sessions(
    request: ConversationBatchDeleteRequest,
    user: AuthUser = Depends(require_current_user),
) -> ConversationBatchDeleteResult:
    return delete_conversations_batch(request.session_ids, owner_id=user.id)


@router.post("/conversations/{session_id}/messages", response_model=ConversationDetailResponse)
def post_conversation_message(
    session_id: str,
    request: ConversationMessageCreateRequest,
    user: AuthUser = Depends(require_current_user),
) -> ConversationDetailResponse:
    settings = get_settings()
    _rate_limit_or_429(user=user, scope="agent_message", limit=settings.rate_limit_agent_per_minute)
    try:
        return handle_user_message(session_id, request, owner_id=user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/conversations/{session_id}/assets", response_model=ConversationDetailResponse)
async def upload_conversation_file(
    session_id: str,
    kind: AssetKind = Form(...),
    file: UploadFile = File(...),
    user: AuthUser = Depends(require_current_user),
) -> ConversationDetailResponse:
    settings = get_settings()
    _rate_limit_or_429(user=user, scope="asset_upload", limit=settings.rate_limit_upload_per_minute)
    try:
        return await upload_conversation_asset(session_id=session_id, kind=kind, upload=file, owner_id=user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/conversations/{session_id}/review-gates/{gate_id}/actions", response_model=ConversationDetailResponse)
def resolve_conversation_review_gate(
    session_id: str,
    gate_id: str,
    request: ConversationReviewActionRequest,
    user: AuthUser = Depends(require_current_user),
) -> ConversationDetailResponse:
    settings = get_settings()
    _rate_limit_or_429(user=user, scope="review_action", limit=settings.rate_limit_agent_per_minute)
    try:
        return handle_review_gate_action(session_id=session_id, gate_id=gate_id, request=request, owner_id=user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/projects", response_model=ProjectRecord)
def create_project(request: ProjectCreateRequest, user: AuthUser = Depends(require_current_user)) -> ProjectRecord:
    project = project_store.create(ProjectCreateRequest(**{**request.model_dump(), "owner_id": user.id}))
    for asset in project.assets:
        memory = register_asset_memory(project, asset)
        project_store.update_asset_metadata(
            project_id=project.id,
            asset_id=asset.id,
            metadata_patch={"asset_memory": memory},
        )
    return project_store.get(project.id)


@router.get("/projects", response_model=list[ProjectRecord])
def list_projects(user: AuthUser = Depends(require_current_user)) -> list[ProjectRecord]:
    return project_store.list(owner_id=user.id)


@router.get("/projects/{project_id}", response_model=ProjectRecord)
def get_project(project_id: str, user: AuthUser = Depends(require_current_user)) -> ProjectRecord:
    try:
        return _project_for_user(project_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/projects/{project_id}", response_model=ProjectRecord)
def update_project(
    project_id: str,
    request: ProjectUpdateRequest,
    user: AuthUser = Depends(require_current_user),
) -> ProjectRecord:
    try:
        existing = _project_for_user(project_id, user)
        if (
            request.workflow_type is not None
            and request.workflow_type != existing.workflow_type
            and existing.status != "created"
        ):
            raise HTTPException(
                status_code=409,
                detail="Workflow type can only be changed before the workflow starts.",
            )
        return project_store.update(project_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, user: AuthUser = Depends(require_current_user)) -> Response:
    try:
        delete_project_workspace(project_id, owner_id=user.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return Response(status_code=204)


@router.get("/assets/orphans")
def list_orphan_asset_dirs(user: AuthUser = Depends(require_current_user)) -> dict[str, object]:
    active_project_ids = {project.id for project in project_store.list(owner_id=user.id)}
    orphans = asset_storage.list_orphan_project_dirs(active_project_ids)
    return {
        "orphan_count": len(orphans),
        "file_count": sum(int(item["file_count"]) for item in orphans),
        "size_bytes": sum(int(item["size_bytes"]) for item in orphans),
        "orphans": orphans,
    }


@router.delete("/assets/orphans")
def cleanup_orphan_asset_dirs(user: AuthUser = Depends(require_current_user)) -> dict[str, object]:
    active_project_ids = {project.id for project in project_store.list(owner_id=user.id)}
    return asset_storage.cleanup_orphan_project_dirs(active_project_ids)


@router.get("/projects/{project_id}/assets/{asset_id}/content")
def get_project_asset_content(
    project_id: str,
    asset_id: str,
    download: bool = Query(default=False),
    user: AuthUser = Depends(require_current_user),
) -> FileResponse:
    try:
        project = _project_for_user(project_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    matched_asset = next((asset for asset in project.assets if asset.id == asset_id), None)
    uri = matched_asset.uri if matched_asset else None
    mime_type = matched_asset.mime_type if matched_asset else None
    filename = matched_asset.filename if matched_asset else f"{asset_id}.png"
    if uri is None:
        generated = _generated_asset_uri(project_id, asset_id)
        uri = generated["uri"] if generated else None
        filename = generated.get("filename", filename) if generated else filename
        mime_type = "image/png"
    if uri is None:
        uri = _generated_asset_file_uri(project_id, asset_id)
        filename = Path(uri).name if uri else filename
        mime_type = "image/png"
    if uri is None:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")

    path = _safe_asset_path(uri)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail=f"Asset file not found: {asset_id}")
    if download:
        return FileResponse(path=path, media_type=mime_type, filename=_archive_safe_name(filename))
    return FileResponse(path=path, media_type=mime_type)


@router.get("/projects/{project_id}/detail", response_model=ProjectDetailResponse)
def get_project_detail(project_id: str, user: AuthUser = Depends(require_current_user)) -> ProjectDetailResponse:
    try:
        project = _project_for_user(project_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return build_project_detail(project)


@router.get("/projects/{project_id}/agent/messages", response_model=list[AgentChatMessage])
def get_project_agent_messages(
    project_id: str,
    user: AuthUser = Depends(require_current_user),
) -> list[AgentChatMessage]:
    try:
        _project_for_user(project_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return list_agent_messages(project_id)


@router.post("/projects/{project_id}/agent/chat", response_model=AgentChatResponse)
def chat_with_project_agent(
    project_id: str,
    request: AgentChatRequest,
    user: AuthUser = Depends(require_current_user),
) -> AgentChatResponse:
    try:
        _project_for_user(project_id, user)
        return run_orchestrator_turn(project_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/projects/{project_id}/archive/download")
def download_project_archive(project_id: str, user: AuthUser = Depends(require_current_user)) -> StreamingResponse:
    try:
        project = _project_for_user(project_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    buffer = _build_project_archive(project)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="project_{project_id}_archive.zip"'},
    )


@router.post("/projects/{project_id}/backups")
def create_project_backup(project_id: str, user: AuthUser = Depends(require_current_user)) -> dict[str, object]:
    try:
        project = _project_for_user(project_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    backup_id = str(uuid4())
    created_at = datetime.now(UTC)
    backup_dir = _backup_dir_for_project(project_id)
    backup_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{backup_id}_project_{project_id}.zip"
    path = backup_dir / filename
    buffer = _build_project_archive(project)
    path.write_bytes(buffer.getvalue())
    return _backup_record(project_id=project_id, backup_id=backup_id, path=path, created_at=created_at)


@router.get("/projects/{project_id}/backups")
def list_project_backups(project_id: str, user: AuthUser = Depends(require_current_user)) -> list[dict[str, object]]:
    try:
        _project_for_user(project_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    backup_dir = _backup_dir_for_project(project_id)
    if not backup_dir.exists():
        return []
    records = []
    for path in sorted(backup_dir.glob("*_project_*.zip"), key=lambda item: item.stat().st_mtime, reverse=True):
        backup_id = path.name.split("_project_", 1)[0]
        records.append(_backup_record(project_id=project_id, backup_id=backup_id, path=path))
    return records


@router.get("/projects/{project_id}/backups/{backup_id}/download")
def download_project_backup(
    project_id: str,
    backup_id: str,
    user: AuthUser = Depends(require_current_user),
) -> FileResponse:
    try:
        _project_for_user(project_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    path = (_backup_dir_for_project(project_id) / f"{backup_id}_project_{project_id}.zip").resolve()
    try:
        path.relative_to(_backup_root().resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Backup path is outside configured storage.") from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Backup not found.")
    return FileResponse(path=path, media_type="application/zip", filename=path.name)


@router.post("/projects/{project_id}/assets", response_model=AssetRef)
async def upload_project_asset(
    project_id: str,
    kind: AssetKind = Form(...),
    file: UploadFile = File(...),
    user: AuthUser = Depends(require_current_user),
) -> AssetRef:
    try:
        _project_for_user(project_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    asset = await asset_storage.save_upload(project_id=project_id, kind=kind, upload=file)
    project_store.add_asset(project_id, asset)
    project = project_store.get(project_id)
    memory = register_asset_memory(project, asset)
    updated = project_store.update_asset_metadata(
        project_id=project_id,
        asset_id=asset.id,
        metadata_patch={"asset_memory": memory, **uploaded_processing_patch(asset)},
    )
    cached, _cache_patch = reuse_completed_cache_for_asset(project_id, updated)
    return cached or updated


@router.patch("/projects/{project_id}/assets/{asset_id}", response_model=AssetRef)
def update_project_asset(
    project_id: str,
    asset_id: str,
    request: AssetUpdateRequest,
    user: AuthUser = Depends(require_current_user),
) -> AssetRef:
    try:
        _project_for_user(project_id, user)
        asset = project_store.update_asset(project_id, asset_id, request)
        project = project_store.get(project_id)
        memory = register_asset_memory(project, asset)
        return project_store.update_asset_metadata(
            project_id=project_id,
            asset_id=asset_id,
            metadata_patch={"asset_memory": memory},
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/projects/{project_id}/assets/{asset_id}", status_code=204)
def delete_project_asset(
    project_id: str,
    asset_id: str,
    user: AuthUser = Depends(require_current_user),
) -> Response:
    try:
        project = _project_for_user(project_id, user)
        asset = next((item for item in project.assets if item.id == asset_id), None)
        if asset is None:
            raise KeyError(f"Asset not found: {asset_id}")
        cancel_asset_processing(project_id, asset_id, reason="asset_deleted")
        asset_storage.delete_asset_file(asset)
        project_store.delete_asset(project_id, asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post("/projects/{project_id}/assets/{asset_id}/analyze", response_model=ImageAssetAnalysis)
def analyze_project_asset(
    project_id: str,
    asset_id: str,
    user: AuthUser = Depends(require_current_user),
) -> ImageAssetAnalysis:
    settings = get_settings()
    _rate_limit_or_429(user=user, scope="asset_analysis", limit=settings.rate_limit_agent_per_minute)
    try:
        project = _project_for_user(project_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    asset = next((item for item in project.assets if item.id == asset_id), None)
    if not asset:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")

    try:
        analysis = analyze_image_asset(
            project_id=project.id,
            asset=asset,
            workflow_type=project.workflow_type,
            category=project.brief.category,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    project_store.update_asset_metadata(
        project_id=project.id,
        asset_id=asset.id,
        metadata_patch={"image_analysis": analysis.model_dump(mode="json")},
    )
    return analysis


@router.post("/projects/{project_id}/assets/{asset_id}/segment", response_model=SegmentationResult)
def segment_project_asset(
    project_id: str,
    asset_id: str,
    mode: str = "auto",
    user: AuthUser = Depends(require_current_user),
) -> SegmentationResult:
    settings = get_settings()
    _rate_limit_or_429(user=user, scope="asset_segmentation", limit=settings.rate_limit_agent_per_minute)
    try:
        project = _project_for_user(project_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    asset = next((item for item in project.assets if item.id == asset_id), None)
    if not asset:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")

    try:
        result = segment_image_asset(project_id=project.id, asset=asset, mode=mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    project_store.add_asset(project.id, result.mask_asset)
    project_store.add_asset(project.id, result.transparent_asset)
    project_store.update_asset_metadata(
        project_id=project.id,
        asset_id=asset.id,
        metadata_patch={"segmentation": result.model_dump(mode="json")},
    )
    return result


@router.post("/workflows/{project_id}/start", response_model=WorkflowResult)
def start_workflow(project_id: str, user: AuthUser = Depends(require_current_user)) -> WorkflowResult:
    settings = get_settings()
    _rate_limit_or_429(user=user, scope="workflow_start", limit=settings.rate_limit_agent_per_minute)
    try:
        project = _project_for_user(project_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result = workflow_engine.start(project)
    project_store.update_status(project_id, result.status)
    return result


@router.post("/workflows/{project_id}/resume", response_model=WorkflowResult)
def resume_workflow(
    project_id: str,
    review: HumanReviewInput,
    user: AuthUser = Depends(require_current_user),
) -> WorkflowResult:
    settings = get_settings()
    _rate_limit_or_429(user=user, scope="workflow_resume", limit=settings.rate_limit_agent_per_minute)
    try:
        _project_for_user(project_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result = workflow_engine.resume(project_id, review)
    project_store.update_status(project_id, result.status)
    return result


@router.post("/memory", response_model=dict[str, str])
def upsert_memory(request: MemoryUpsertRequest, user: AuthUser = Depends(require_current_user)) -> dict[str, str]:
    if request.project_id:
        try:
            _project_for_user(request.project_id, user)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    memory_id = get_memory_store().upsert(request)
    return {"memory_id": memory_id}


@router.post("/memory/search", response_model=list[MemorySearchResult])
def search_memory(request: MemorySearchRequest, user: AuthUser = Depends(require_current_user)) -> list[MemorySearchResult]:
    if request.project_id:
        try:
            _project_for_user(request.project_id, user)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return get_memory_store().search(
        query=request.query,
        limit=request.limit,
        memory_type=request.memory_type,
        project_id=request.project_id,
        brand_id=request.brand_id,
        category=request.category,
        workflow_type=request.workflow_type,
    )


@router.get("/tasks")
def list_background_tasks(
    project_id: str | None = Query(default=None),
    user: AuthUser = Depends(require_current_user),
) -> list[dict[str, object]]:
    if project_id:
        try:
            _project_for_user(project_id, user)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    jobs = background_task_queue.store.list(owner_id=user.id, project_id=project_id)
    return [
        {
            "id": job.id,
            "kind": job.kind,
            "project_id": job.project_id,
            "status": job.status,
            "payload": job.payload,
            "error": job.error,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "heartbeat_at": job.heartbeat_at,
        }
        for job in jobs
    ]


@router.post("/tasks/{job_id}/cancel")
def cancel_background_task(
    job_id: str,
    user: AuthUser = Depends(require_current_user),
) -> dict[str, object]:
    try:
        job = background_task_queue.store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if job.project_id:
        try:
            _project_for_user(job.project_id, user)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    elif job.owner_id and job.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Task is not owned by current user.")
    cancelled = background_task_queue.cancel(job_id, reason="cancelled_by_user")
    return {
        "id": cancelled.id,
        "status": cancelled.status,
        "error": cancelled.error,
        "finished_at": cancelled.finished_at,
        "heartbeat_at": cancelled.heartbeat_at,
    }


@router.get("/knowledge", response_model=list[KnowledgeBaseEntry])
def list_knowledge_entries(
    status: KnowledgeStatus | None = Query(default=None),
    domain: KnowledgeDomain | None = Query(default=None),
    workflow_type: KnowledgeWorkflowType | None = Query(default=None),
) -> list[KnowledgeBaseEntry]:
    return knowledge_store.list(status=status, domain=domain, workflow_type=workflow_type)


@router.post("/knowledge", response_model=KnowledgeBaseEntry)
def create_knowledge_entry(request: KnowledgeBaseCreateRequest) -> KnowledgeBaseEntry:
    try:
        return knowledge_store.create(request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/knowledge/{entry_id}", response_model=KnowledgeBaseEntry)
def get_knowledge_entry(entry_id: str) -> KnowledgeBaseEntry:
    try:
        return knowledge_store.get(entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/knowledge/{entry_id}", response_model=KnowledgeBaseEntry)
def update_knowledge_entry(entry_id: str, request: KnowledgeBaseUpdateRequest) -> KnowledgeBaseEntry:
    try:
        return knowledge_store.update(entry_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/knowledge/{entry_id}", response_model=KnowledgeBaseEntry)
def delete_knowledge_entry(entry_id: str) -> KnowledgeBaseEntry:
    try:
        return knowledge_store.delete(entry_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/knowledge/search", response_model=list[KnowledgeSearchResult])
def search_knowledge_entries(request: KnowledgeSearchRequest) -> list[KnowledgeSearchResult]:
    return search_knowledge(request)


@router.post("/projects/{project_id}/knowledge/preview", response_model=ProjectKnowledgePreviewResponse)
def preview_project_knowledge(
    project_id: str,
    limit: int = Query(default=5, ge=1, le=20),
    user: AuthUser = Depends(require_current_user),
) -> ProjectKnowledgePreviewResponse:
    try:
        project = _project_for_user(project_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    context = build_project_knowledge_context(project, domain="packaging", limit=limit)
    result_ids = {item["id"] for item in context.get("results", []) if isinstance(item, dict)}
    results = [
        result
        for result in search_knowledge(
            KnowledgeSearchRequest(
                query=str(context.get("query") or ""),
                workflow_type=project.workflow_type,
                domain="packaging",
                status="active",
                category=project.brief.category,
                limit=limit,
            )
        )
        if result.entry.id in result_ids
    ]
    return ProjectKnowledgePreviewResponse(
        project_id=project_id,
        query=str(context.get("query") or ""),
        results=results,
        injected_context=context,
    )


@router.get("/prompts", response_model=list[PromptVersion])
def list_prompts() -> list[PromptVersion]:
    return get_prompt_registry().list(include_content=False)


@router.get("/prompts/{prompt_name}", response_model=PromptVersion)
def get_prompt(prompt_name: str) -> PromptVersion:
    try:
        return get_prompt_registry().get(prompt_name, include_content=True)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/golden/fixtures", response_model=list[GoldenFixtureSummary])
def list_golden_regression_fixtures() -> list[GoldenFixtureSummary]:
    return list_golden_fixtures()


@router.post("/golden/fixtures/{fixture_name}/run", response_model=GoldenRunResult)
def run_golden_regression_fixture(fixture_name: str) -> GoldenRunResult:
    try:
        return run_golden_fixture(fixture_name)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/integrations/probe", response_model=IntegrationProbeResult)
def probe_integrations(request: IntegrationProbeRequest) -> IntegrationProbeResult:
    return run_integration_probe(request)


@router.get("/projects/{project_id}/audit", response_model=list[AuditRecord])
def list_project_audit_records(
    project_id: str,
    record_type: AuditRecordType | None = Query(default=None),
    user: AuthUser = Depends(require_current_user),
) -> list[AuditRecord]:
    try:
        _project_for_user(project_id, user)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return audit_store.list_records(project_id=project_id, record_type=record_type)


def _generated_asset_uri(project_id: str, asset_id: str) -> dict[str, str] | None:
    for record in audit_store.list_records(project_id=project_id, record_type="agent_output"):
        payload = record.payload
        candidates = []
        if isinstance(payload.get("generated_outputs"), dict):
            candidates.extend(payload["generated_outputs"].get("items", []))
        if isinstance(payload.get("items"), list):
            candidates.extend(payload["items"])
        for item in candidates:
            if not isinstance(item, dict):
                continue
            layout_spec = item.get("layout_spec") if isinstance(item.get("layout_spec"), dict) else {}
            id_fields = {
                str(item.get("asset_id") or ""),
                str(layout_spec.get("base_asset_id") or ""),
                str(layout_spec.get("composed_asset_id") or ""),
            }
            if asset_id in id_fields:
                uri = _first_existing_generated_uri(item, asset_id=asset_id)
                if uri:
                    name = _archive_safe_name(str(item.get("name") or asset_id))
                    return {"uri": uri, "filename": f"{name}.png"}
    return None


def _first_existing_generated_uri(item: dict, asset_id: str = "") -> str:
    layout_spec = item.get("layout_spec") if isinstance(item.get("layout_spec"), dict) else {}
    if asset_id and asset_id == str(layout_spec.get("composed_asset_id") or ""):
        preferred = str(layout_spec.get("composed_asset_uri") or "")
        if preferred and _asset_uri_exists(preferred):
            return preferred
    if asset_id and asset_id == str(layout_spec.get("base_asset_id") or item.get("asset_id") or ""):
        preferred = str(layout_spec.get("base_asset_uri") or item.get("uri") or "")
        if preferred and _asset_uri_exists(preferred):
            return preferred
    candidates = [
        str(item.get("uri") or ""),
        str(layout_spec.get("base_asset_uri") or ""),
        str(layout_spec.get("composed_asset_uri") or ""),
    ]
    for uri in candidates:
        if uri and _asset_uri_exists(uri):
            return uri
    return next((uri for uri in candidates if uri), "")


def _asset_uri_exists(uri: str) -> bool:
    try:
        return _safe_asset_path(uri).exists()
    except HTTPException:
        return False


def _generated_asset_file_uri(project_id: str, asset_id: str) -> str | None:
    project_dir = (asset_storage.root / project_id).resolve()
    storage_root = asset_storage.root.resolve()
    try:
        project_dir.relative_to(storage_root)
    except ValueError:
        return None
    if not project_dir.exists() or not project_dir.is_dir():
        return None
    matches = sorted(project_dir.glob(f"{asset_id}_*.png"), key=lambda item: item.stat().st_mtime, reverse=True)
    return str(matches[0]) if matches else None


def _build_project_archive(project: ProjectRecord) -> BytesIO:
    detail = build_project_detail(project)
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "project": project.model_dump(mode="json"),
                    "progress": detail.progress.model_dump(mode="json"),
                    "latest_generated_outputs": detail.latest_generated_outputs,
                    "latest_qc_report": detail.latest_qc_report,
                    "latest_archive": detail.latest_archive,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "audit_records.json",
            json.dumps(
                [record.model_dump(mode="json") for record in detail.audit_records],
                ensure_ascii=False,
                indent=2,
            ),
        )
        for index, item in enumerate(detail.latest_generated_outputs.get("items", []), start=1):
            layout_spec = item.get("layout_spec") if isinstance(item.get("layout_spec"), dict) else {}
            target_asset_id = str(layout_spec.get("base_asset_id") or item.get("asset_id") or "")
            generated = _generated_asset_uri(project.id, target_asset_id)
            uri = (generated["uri"] if generated else "") or str(item.get("uri") or "")
            if not uri:
                continue
            path = _safe_asset_path(str(uri))
            if path.exists() and path.is_file():
                name = _archive_safe_name(str(item.get("name") or f"output_{index}"))
                suffix = path.suffix or ".png"
                archive.write(path, f"outputs/{index:02d}_{name}{suffix}")
        for index, asset in enumerate(project.assets, start=1):
            path = _safe_asset_path(asset.uri)
            if path.exists() and path.is_file():
                name = _archive_safe_name(asset.filename)
                archive.write(path, f"source_assets/{index:02d}_{asset.kind}_{name}")
    buffer.seek(0)
    return buffer


def _backup_root() -> Path:
    return (asset_storage.root.parent / "backups").resolve()


def _backup_dir_for_project(project_id: str) -> Path:
    root = _backup_root()
    path = (root / _archive_safe_name(project_id)).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Backup path is outside configured storage.") from exc
    return path


def _backup_record(
    *,
    project_id: str,
    backup_id: str,
    path: Path,
    created_at: datetime | None = None,
) -> dict[str, object]:
    stat = path.stat()
    created = created_at or datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    return {
        "id": backup_id,
        "project_id": project_id,
        "filename": path.name,
        "size_bytes": stat.st_size,
        "created_at": created.isoformat(),
        "download_url": f"/api/projects/{project_id}/backups/{backup_id}/download",
    }


def _safe_asset_path(uri: str) -> Path:
    path = Path(uri)
    if not path.is_absolute():
        path = Path.cwd() / path
    resolved = path.resolve()
    storage_root = asset_storage.root.resolve()
    try:
        resolved.relative_to(storage_root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Asset path is outside configured storage.") from exc
    return resolved


def _archive_safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {".", "_", "-"} else "_" for char in value.strip())
    return cleaned[:80] or "asset"
