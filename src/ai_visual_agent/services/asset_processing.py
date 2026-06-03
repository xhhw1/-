from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ai_visual_agent.domain import AssetRef
from ai_visual_agent.services.project_store import project_store


DOCUMENT_PARSE_VERSION = "document_parser_v1"
IMAGE_ANALYSIS_VERSION = "image_understanding_v1"


def parser_version_for_tool(tool: str) -> str:
    if tool == "image_understanding":
        return IMAGE_ANALYSIS_VERSION
    return DOCUMENT_PARSE_VERSION


def uploaded_processing_patch(asset: AssetRef, *, tool: str | None = None) -> dict[str, Any]:
    parser_name = tool or _default_tool(asset)
    return {
        "processing": {
            "status": "uploaded",
            "parser_name": parser_name,
            "parser_version": parser_version_for_tool(parser_name),
            "progress": 0,
            "cancel_requested": False,
            "updated_at": _now(),
        }
    }


def reuse_completed_cache_for_asset(project_id: str, asset: AssetRef) -> tuple[AssetRef | None, dict[str, Any] | None]:
    tool = _default_tool(asset)
    result_key = "image_analysis" if tool == "image_understanding" else "document_parse"
    error_key = "image_analysis_error" if tool == "image_understanding" else "document_parse_error"
    return reuse_global_cache_if_available(
        project_id=project_id,
        asset=asset,
        tool=tool,
        result_key=result_key,
        error_key=error_key,
    )


def processing_valid(asset: AssetRef, *, tool: str, result_key: str) -> bool:
    processing = _processing(asset)
    return (
        processing.get("status") == "completed"
        and processing.get("parser_name") == tool
        and processing.get("parser_version") == parser_version_for_tool(tool)
        and isinstance(asset.metadata.get(result_key), dict)
    )


def mark_processing_queued(project_id: str, asset_id: str, *, tool: str, reason: str = "") -> AssetRef:
    return _patch_processing(project_id, asset_id, tool=tool, status="queued", progress=0, extra={"reason": reason})


def mark_processing_running(project_id: str, asset_id: str, *, tool: str, progress: int = 5) -> AssetRef:
    return _patch_processing(project_id, asset_id, tool=tool, status="running", progress=progress)


def mark_processing_completed(
    project_id: str,
    asset_id: str,
    *,
    tool: str,
    result_ref: str,
    progress: int = 100,
    extra: dict[str, Any] | None = None,
) -> AssetRef:
    payload = {"result_ref": result_ref, "completed_at": _now()}
    if extra:
        payload.update(extra)
    return _patch_processing(project_id, asset_id, tool=tool, status="completed", progress=progress, extra=payload)


def mark_processing_failed(project_id: str, asset_id: str, *, tool: str, error: str) -> AssetRef:
    return _patch_processing(
        project_id,
        asset_id,
        tool=tool,
        status="failed",
        progress=0,
        extra={"error": error, "failed_at": _now()},
    )


def cancel_asset_processing(project_id: str, asset_id: str, *, reason: str = "asset_deleted") -> AssetRef:
    asset = _asset(project_id, asset_id)
    processing = _processing(asset)
    status = str(processing.get("status") or "uploaded")
    tool = str(processing.get("parser_name") or _default_tool(asset))
    if status in {"queued", "running", "uploaded"}:
        return _patch_processing(
            project_id,
            asset_id,
            tool=tool,
            status="cancelled",
            progress=int(processing.get("progress") or 0),
            extra={"cancel_requested": True, "cancelled_at": _now(), "cancel_reason": reason},
        )
    return asset


def cancellation_requested(asset: AssetRef) -> bool:
    processing = _processing(asset)
    return bool(processing.get("cancel_requested")) or processing.get("status") == "cancelled"


def reuse_global_cache_if_available(
    *,
    project_id: str,
    asset: AssetRef,
    tool: str,
    result_key: str,
    error_key: str,
) -> tuple[AssetRef | None, dict[str, Any] | None]:
    sha256 = asset.metadata.get("sha256")
    if not sha256:
        return None, None
    version = parser_version_for_tool(tool)
    for project in project_store.list():
        for candidate in project.assets:
            if project.id == project_id and candidate.id == asset.id:
                continue
            if candidate.metadata.get("sha256") != sha256:
                continue
            candidate_processing = _processing(candidate)
            if (
                candidate_processing.get("status") != "completed"
                or candidate_processing.get("parser_name") != tool
                or candidate_processing.get("parser_version") != version
                or not isinstance(candidate.metadata.get(result_key), dict)
            ):
                continue
            patch = {
                result_key: candidate.metadata[result_key],
                error_key: None,
                "processing": {
                    "status": "completed",
                    "parser_name": tool,
                    "parser_version": version,
                    "progress": 100,
                    "result_ref": candidate_processing.get("result_ref") or result_key,
                    "cache_hit": True,
                    "cache_source_asset_id": candidate.id,
                    "cache_source_project_id": project.id,
                    "completed_at": candidate_processing.get("completed_at") or _now(),
                    "updated_at": _now(),
                },
            }
            updated = project_store.update_asset_metadata(project_id, asset.id, patch)
            return updated, patch
    return None, None


def _asset(project_id: str, asset_id: str) -> AssetRef:
    project = project_store.get(project_id)
    asset = next((item for item in project.assets if item.id == asset_id), None)
    if not asset:
        raise KeyError(f"Asset not found: {asset_id}")
    return asset


def _patch_processing(
    project_id: str,
    asset_id: str,
    *,
    tool: str,
    status: str,
    progress: int,
    extra: dict[str, Any] | None = None,
) -> AssetRef:
    processing = {
        "status": status,
        "parser_name": tool,
        "parser_version": parser_version_for_tool(tool),
        "progress": max(0, min(100, int(progress))),
        "cancel_requested": False,
        "updated_at": _now(),
    }
    if extra:
        processing.update(extra)
    return project_store.update_asset_metadata(project_id, asset_id, {"processing": processing})


def _processing(asset: AssetRef) -> dict[str, Any]:
    value = asset.metadata.get("processing")
    return value if isinstance(value, dict) else {}


def _default_tool(asset: AssetRef) -> str:
    mime = (asset.mime_type or "").lower()
    if mime.startswith("image/"):
        return "image_understanding"
    return "document_parser"


def _now() -> str:
    return datetime.now(UTC).isoformat()
