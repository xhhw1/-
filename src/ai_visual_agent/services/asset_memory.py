from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_visual_agent.domain import AssetRef, MemoryUpsertRequest, ProjectRecord
from ai_visual_agent.services.audit_store import audit_store
from ai_visual_agent.services.memory_store import get_memory_store


PRODUCT_REFERENCE_KINDS = {"transparent_product_image", "product_image"}


def register_asset_memory(project: ProjectRecord, asset: AssetRef) -> dict[str, Any]:
    """Register an uploaded asset as agent-readable file inventory memory."""

    profile = _profile_for_asset(asset)
    summary = _asset_summary(project, asset, profile)
    memory_id = get_memory_store().upsert(
        MemoryUpsertRequest(
            text=summary,
            memory_type="asset_registry",
            project_id=project.id,
            category=project.brief.category,
            workflow_type=project.workflow_type,
            asset_id=asset.id,
            source_type="asset_registry",
            metadata={
                "asset_kind": asset.kind,
                "filename": asset.filename,
                "mime_type": asset.mime_type,
                "asset_role": profile["role"],
                "agent_tool_hints": profile["tools"],
                "candidate_reference": profile["candidate_reference"],
                "file_size_bytes": asset.metadata.get("size_bytes"),
            },
        )
    )
    metadata = {
        "memory_id": memory_id,
        "source_type": "asset_registry",
        "role": profile["role"],
        "summary": summary,
        "agent_tool_hints": profile["tools"],
        "candidate_reference": profile["candidate_reference"],
    }
    audit_store.record(
        project_id=project.id,
        record_type="agent_output",
        stage="register_asset_memory",
        payload={
            "asset_id": asset.id,
            "filename": asset.filename,
            "kind": asset.kind,
            "memory_id": memory_id,
            "role": profile["role"],
            "agent_tool_hints": profile["tools"],
        },
    )
    return metadata


def project_file_memory_context(project: ProjectRecord) -> list[dict[str, Any]]:
    """Return current file inventory context for planner/agent prompts."""

    active_asset_ids = {asset.id for asset in project.assets}
    context: list[dict[str, Any]] = []
    seen: set[str] = set()

    for asset in project.assets:
        memory = asset.metadata.get("asset_memory")
        if isinstance(memory, dict):
            context.append(_context_item_from_asset(asset, memory))
            seen.add(asset.id)

    results = get_memory_store().search(
        query="asset registry file inventory product reference competitor vi logo document",
        limit=50,
        project_id=project.id,
        memory_type="asset_registry",
        source_type="asset_registry",
    )
    for result in results:
        asset_id = str(result.payload.get("asset_id") or "")
        if not asset_id or asset_id not in active_asset_ids or asset_id in seen:
            continue
        asset = next((item for item in project.assets if item.id == asset_id), None)
        if not asset:
            continue
        context.append(
            {
                "asset_id": asset_id,
                "filename": result.payload.get("filename") or asset.filename,
                "kind": result.payload.get("asset_kind") or asset.kind,
                "mime_type": result.payload.get("mime_type") or asset.mime_type,
                "role": result.payload.get("asset_role") or "registered_asset",
                "summary": result.text,
                "memory_id": result.id,
                "agent_tool_hints": result.payload.get("agent_tool_hints") or [],
                "candidate_reference": bool(result.payload.get("candidate_reference")),
                "preferred_product_reference": bool(asset.metadata.get("preferred_product_reference")),
            }
        )
        seen.add(asset_id)

    return sorted(context, key=_context_sort_key)


def _context_item_from_asset(asset: AssetRef, memory: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": asset.id,
        "filename": asset.filename,
        "kind": asset.kind,
        "mime_type": asset.mime_type,
        "role": memory.get("role") or "registered_asset",
        "summary": memory.get("summary") or "",
        "memory_id": memory.get("memory_id") or "",
        "agent_tool_hints": memory.get("agent_tool_hints") or [],
        "candidate_reference": bool(memory.get("candidate_reference")),
        "preferred_product_reference": bool(asset.metadata.get("preferred_product_reference")),
    }


def _profile_for_asset(asset: AssetRef) -> dict[str, Any]:
    kind = asset.kind
    if kind in {"product_ppt", "product_pdf"}:
        return {
            "role": "product_document",
            "tools": ["parse_document_file", "extract_product_metadata", "retrieve_product_doc_memory"],
            "candidate_reference": False,
        }
    if kind == "transparent_product_image":
        return {
            "role": "primary_product_cutout",
            "tools": ["use_as_product_reference", "image_edit_reference", "product_consistency_qc"],
            "candidate_reference": True,
        }
    if kind == "product_image":
        return {
            "role": "product_reference_image",
            "tools": ["analyze_image_asset", "segment_image_asset", "image_edit_reference", "product_consistency_qc"],
            "candidate_reference": True,
        }
    if kind in {"competitor_image", "competitor_packaging", "competitor_detail_page"}:
        return {
            "role": "competitor_visual_reference",
            "tools": ["analyze_image_asset", "extract_competitor_hooks", "retrieve_competitor_memory"],
            "candidate_reference": False,
        }
    if kind == "competitor_video":
        return {
            "role": "competitor_video_reference",
            "tools": ["sample_video_keyframes", "analyze_competitor_video", "extract_competitor_hooks"],
            "candidate_reference": False,
        }
    if kind == "vi_document":
        return {
            "role": "brand_vi_document",
            "tools": ["parse_document_file", "analyze_image_asset", "vi_guardian"],
            "candidate_reference": False,
        }
    if kind == "logo":
        return {
            "role": "brand_logo_asset",
            "tools": ["analyze_image_asset", "vi_guardian", "layout_overlay_logo"],
            "candidate_reference": False,
        }
    if kind == "mask_image":
        return {
            "role": "segmentation_mask",
            "tools": ["segmentation_quality_check"],
            "candidate_reference": False,
        }
    return {
        "role": "supporting_asset",
        "tools": ["manual_review", "retrieve_asset_registry"],
        "candidate_reference": False,
    }


def _asset_summary(project: ProjectRecord, asset: AssetRef, profile: dict[str, Any]) -> str:
    file_size = asset.metadata.get("size_bytes")
    size_text = f"{file_size} bytes" if file_size else "unknown size"
    suffix = Path(asset.filename).suffix.lower() or "unknown extension"
    return "\n".join(
        [
            "Asset registry memory.",
            f"Project: {project.id}.",
            f"Workflow: {project.workflow_type}.",
            f"Category: {project.brief.category or 'unknown'}.",
            f"Core product: {project.brief.core_product_definition or 'unknown'}.",
            f"Asset id: {asset.id}.",
            f"Filename: {asset.filename}.",
            f"Kind: {asset.kind}.",
            f"Mime type: {asset.mime_type or 'unknown'}.",
            f"Extension: {suffix}.",
            f"File size: {size_text}.",
            f"Agent role: {profile['role']}.",
            f"Available tools: {', '.join(profile['tools'])}.",
            f"Candidate product reference: {profile['candidate_reference']}.",
        ]
    )


def _context_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    if item.get("preferred_product_reference"):
        priority = 0
    elif item.get("kind") == "transparent_product_image":
        priority = 1
    elif item.get("kind") == "product_image":
        priority = 2
    elif item.get("kind") in {"product_ppt", "product_pdf"}:
        priority = 3
    elif str(item.get("kind") or "").startswith("competitor"):
        priority = 4
    elif item.get("kind") in {"vi_document", "logo"}:
        priority = 5
    else:
        priority = 9
    return priority, str(item.get("filename") or "")
