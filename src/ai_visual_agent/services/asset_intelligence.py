from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_visual_agent.domain import AssetRef, MemoryUpsertRequest, ProjectRecord
from ai_visual_agent.services.asset_processing import (
    cancellation_requested,
    mark_processing_completed,
    mark_processing_failed,
    mark_processing_running,
    processing_valid,
    reuse_global_cache_if_available,
)
from ai_visual_agent.services.audit_store import audit_store
from ai_visual_agent.services.image_analysis import analyze_image_asset, is_image_asset
from ai_visual_agent.services.memory_store import get_memory_store
from ai_visual_agent.services.project_store import project_store
from ai_visual_agent.services.storage import asset_storage
from ai_visual_agent.tools.document_tools import parse_document_file


DOCUMENT_KINDS = {"product_ppt", "product_pdf", "vi_document"}


def enrich_project_assets(
    *,
    project_id: str,
    workflow_type: str | None = None,
    asset_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    project = project_store.get(project_id)
    target_ids = set(asset_ids or [])
    reports: list[dict[str, Any]] = []
    for asset in list(project.assets):
        if target_ids and asset.id not in target_ids:
            continue
        reports.extend(_enrich_asset(project=project, asset=asset, workflow_type=workflow_type))
    return reports


def build_project_evidence_context(project: ProjectRecord) -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for asset in project.assets:
        metadata = asset.metadata or {}
        document_parse = metadata.get("document_parse") if isinstance(metadata.get("document_parse"), dict) else None
        image_analysis = metadata.get("image_analysis") if isinstance(metadata.get("image_analysis"), dict) else None
        parse_error = metadata.get("document_parse_error")
        image_error = metadata.get("image_analysis_error")
        if document_parse:
            documents.append(_document_evidence(asset, document_parse))
        elif _is_document_asset(asset):
            missing.append({"asset_id": asset.id, "filename": asset.filename, "reason": str(parse_error or "未解析")})
        if image_analysis:
            images.append(_image_evidence(asset, image_analysis))
        elif is_image_asset(asset):
            missing.append({"asset_id": asset.id, "filename": asset.filename, "reason": str(image_error or "未理解")})
    return {
        "documents": documents,
        "images": images,
        "asset_count": len(project.assets),
        "parsed_document_count": len(documents),
        "analyzed_image_count": len(images),
        "missing_or_failed_assets": missing,
        "evidence_digest": _evidence_digest(documents=documents, images=images, missing=missing),
    }


def evidence_summary_for_review(evidence: dict[str, Any]) -> dict[str, Any]:
    docs = evidence.get("documents") if isinstance(evidence.get("documents"), list) else []
    images = evidence.get("images") if isinstance(evidence.get("images"), list) else []
    missing = evidence.get("missing_or_failed_assets") if isinstance(evidence.get("missing_or_failed_assets"), list) else []
    return {
        "parsed_documents": [
            {
                "filename": item.get("filename"),
                "parser": item.get("parser"),
                "page_count": item.get("page_count"),
                "highlights": item.get("highlights", [])[:4],
            }
            for item in docs
        ],
        "analyzed_images": [
            {
                "filename": item.get("filename"),
                "role": item.get("image_role"),
                "engine": item.get("engine"),
                "summary": item.get("summary"),
            }
            for item in images
        ],
        "missing_or_failed_assets": missing[:6],
    }


def compact_evidence_for_downstream(evidence: dict[str, Any]) -> dict[str, Any]:
    """Small, stage-safe evidence packet for strategy/prompt/design agents."""
    docs = evidence.get("documents") if isinstance(evidence.get("documents"), list) else []
    images = evidence.get("images") if isinstance(evidence.get("images"), list) else []
    missing = evidence.get("missing_or_failed_assets") if isinstance(evidence.get("missing_or_failed_assets"), list) else []
    return {
        "parsed_document_count": evidence.get("parsed_document_count", 0),
        "analyzed_image_count": evidence.get("analyzed_image_count", 0),
        "documents": [
            {
                "asset_id": item.get("asset_id"),
                "filename": item.get("filename"),
                "kind": item.get("kind"),
                "page_count": item.get("page_count"),
                "highlights": item.get("highlights", [])[:6],
            }
            for item in docs
        ],
        "images": [
            {
                "asset_id": item.get("asset_id"),
                "filename": item.get("filename"),
                "kind": item.get("kind"),
                "image_role": item.get("image_role"),
                "summary": item.get("summary"),
                "product_appearance": item.get("product_appearance", [])[:6],
                "visible_accessories": item.get("visible_accessories", [])[:6],
                "play_clues": item.get("play_clues", [])[:6],
                "risks": item.get("risks", [])[:4],
            }
            for item in images
        ],
        "missing_or_failed_assets": missing[:6],
        "evidence_digest": _compact(str(evidence.get("evidence_digest") or ""), 1800),
    }


def _enrich_asset(
    *,
    project: ProjectRecord,
    asset: AssetRef,
    workflow_type: str | None,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    if _is_document_asset(asset):
        reports.append(_ensure_document_parse(project=project, asset=asset, workflow_type=workflow_type))
    if is_image_asset(asset):
        reports.append(_ensure_image_analysis(project=project, asset=asset, workflow_type=workflow_type))
    return [report for report in reports if report]


def _ensure_document_parse(
    *,
    project: ProjectRecord,
    asset: AssetRef,
    workflow_type: str | None,
) -> dict[str, Any]:
    if processing_valid(asset, tool="document_parser", result_key="document_parse"):
        parsed = asset.metadata["document_parse"]
        return {
            "asset_id": asset.id,
            "filename": asset.filename,
            "tool": "document_parser",
            "status": "cached",
            "parser": parsed.get("parser"),
            "page_count": len(parsed.get("pages", []) or []),
        }
    cached_asset, _patch = reuse_global_cache_if_available(
        project_id=project.id,
        asset=asset,
        tool="document_parser",
        result_key="document_parse",
        error_key="document_parse_error",
    )
    if cached_asset and isinstance(cached_asset.metadata.get("document_parse"), dict):
        parsed = cached_asset.metadata["document_parse"]
        return {
            "asset_id": cached_asset.id,
            "filename": cached_asset.filename,
            "tool": "document_parser",
            "status": "cached",
            "parser": parsed.get("parser"),
            "page_count": len(parsed.get("pages", []) or []),
            "cache_hit": True,
        }
    if cancellation_requested(asset):
        return {
            "asset_id": asset.id,
            "filename": asset.filename,
            "tool": "document_parser",
            "status": "cancelled",
        }
    try:
        mark_processing_running(project.id, asset.id, tool="document_parser", progress=10)
        file_path = asset_storage.ensure_local_file(asset)
        parsed = parse_document_file(file_id=asset.id, file_uri=str(file_path), file_type=asset.mime_type or asset.filename)
        current = next((item for item in project_store.get(project.id).assets if item.id == asset.id), asset)
        if cancellation_requested(current):
            return {
                "asset_id": asset.id,
                "filename": asset.filename,
                "tool": "document_parser",
                "status": "cancelled",
            }
        project_store.update_asset_metadata(project.id, asset.id, {"document_parse": parsed, "document_parse_error": None})
        mark_processing_completed(
            project.id,
            asset.id,
            tool="document_parser",
            result_ref="metadata.document_parse",
            extra={"parser": parsed.get("parser"), "page_count": len(parsed.get("pages", []) or [])},
        )
        _write_document_memory(project=project, asset=asset, parsed=parsed, workflow_type=workflow_type)
        audit_store.record(
            project_id=project.id,
            record_type="agent_output",
            stage="parse_conversation_document",
            payload={"asset_id": asset.id, "filename": asset.filename, "parser": parsed.get("parser"), "page_count": len(parsed.get("pages", []) or [])},
        )
        return {
            "asset_id": asset.id,
            "filename": asset.filename,
            "tool": "document_parser",
            "status": "completed",
            "parser": parsed.get("parser"),
            "page_count": len(parsed.get("pages", []) or []),
        }
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        project_store.update_asset_metadata(project.id, asset.id, {"document_parse_error": error})
        mark_processing_failed(project.id, asset.id, tool="document_parser", error=error)
        audit_store.record(
            project_id=project.id,
            record_type="agent_output",
            stage="parse_conversation_document",
            payload={"asset_id": asset.id, "filename": asset.filename, "error": error},
        )
        return {
            "asset_id": asset.id,
            "filename": asset.filename,
            "tool": "document_parser",
            "status": "failed",
            "error": error,
        }


def _ensure_image_analysis(
    *,
    project: ProjectRecord,
    asset: AssetRef,
    workflow_type: str | None,
) -> dict[str, Any]:
    if processing_valid(asset, tool="image_understanding", result_key="image_analysis"):
        analysis = asset.metadata["image_analysis"]
        return {
            "asset_id": asset.id,
            "filename": asset.filename,
            "tool": "image_understanding",
            "status": "cached",
            "engine": (analysis.get("understanding") or {}).get("engine"),
            "role": analysis.get("image_role"),
        }
    cached_asset, _patch = reuse_global_cache_if_available(
        project_id=project.id,
        asset=asset,
        tool="image_understanding",
        result_key="image_analysis",
        error_key="image_analysis_error",
    )
    if cached_asset and isinstance(cached_asset.metadata.get("image_analysis"), dict):
        analysis = cached_asset.metadata["image_analysis"]
        return {
            "asset_id": cached_asset.id,
            "filename": cached_asset.filename,
            "tool": "image_understanding",
            "status": "cached",
            "engine": (analysis.get("understanding") or {}).get("engine"),
            "role": analysis.get("image_role"),
            "cache_hit": True,
        }
    if cancellation_requested(asset):
        return {
            "asset_id": asset.id,
            "filename": asset.filename,
            "tool": "image_understanding",
            "status": "cancelled",
        }
    try:
        mark_processing_running(project.id, asset.id, tool="image_understanding", progress=10)
        analysis = analyze_image_asset(
            project_id=project.id,
            asset=asset,
            workflow_type=workflow_type or project.workflow_type,
            category=project.brief.category,
        )
        current = next((item for item in project_store.get(project.id).assets if item.id == asset.id), asset)
        if cancellation_requested(current):
            return {
                "asset_id": asset.id,
                "filename": asset.filename,
                "tool": "image_understanding",
                "status": "cancelled",
            }
        project_store.update_asset_metadata(project.id, asset.id, {"image_analysis": analysis.model_dump(mode="json"), "image_analysis_error": None})
        mark_processing_completed(
            project.id,
            asset.id,
            tool="image_understanding",
            result_ref="metadata.image_analysis",
            extra={"engine": analysis.understanding.engine if analysis.understanding else "", "role": analysis.image_role},
        )
        return {
            "asset_id": asset.id,
            "filename": asset.filename,
            "tool": "image_understanding",
            "status": "completed",
            "engine": analysis.understanding.engine if analysis.understanding else "",
            "role": analysis.image_role,
            "warnings": analysis.warnings,
        }
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        project_store.update_asset_metadata(project.id, asset.id, {"image_analysis_error": error})
        mark_processing_failed(project.id, asset.id, tool="image_understanding", error=error)
        audit_store.record(
            project_id=project.id,
            record_type="agent_output",
            stage="analyze_conversation_image",
            payload={"asset_id": asset.id, "filename": asset.filename, "error": error},
        )
        return {
            "asset_id": asset.id,
            "filename": asset.filename,
            "tool": "image_understanding",
            "status": "failed",
            "error": error,
        }


def _document_evidence(asset: AssetRef, parsed: dict[str, Any]) -> dict[str, Any]:
    pages = parsed.get("pages") if isinstance(parsed.get("pages"), list) else []
    highlights = []
    page_summaries = []
    full_text_parts = []
    for page in pages[:18]:
        text = str(page.get("text") or page.get("ocr_text") or "")
        summary = str(page.get("semantic_summary") or _compact(text, 180))
        title = str(page.get("title") or f"Page {page.get('page_index') or ''}").strip()
        if summary:
            highlights.append(f"{title}: {summary}" if title else summary)
        page_summaries.append(
            {
                "page_index": page.get("page_index"),
                "title": title,
                "summary": summary,
                "text_excerpt": _compact(text, 1200),
            }
        )
        if text.strip():
            full_text_parts.append(text)
    return {
        "asset_id": asset.id,
        "filename": asset.filename,
        "kind": asset.kind,
        "parser": parsed.get("parser"),
        "page_count": len(pages),
        "highlights": highlights[:10],
        "pages": page_summaries,
        "text_excerpt": _compact("\n".join(full_text_parts), 10000),
    }


def _image_evidence(asset: AssetRef, analysis: dict[str, Any]) -> dict[str, Any]:
    understanding = analysis.get("understanding") if isinstance(analysis.get("understanding"), dict) else {}
    ocr = analysis.get("ocr") if isinstance(analysis.get("ocr"), dict) else {}
    return {
        "asset_id": asset.id,
        "filename": asset.filename,
        "kind": asset.kind,
        "image_role": analysis.get("image_role"),
        "engine": understanding.get("engine"),
        "width": analysis.get("width"),
        "height": analysis.get("height"),
        "summary": analysis.get("semantic_summary") or understanding.get("summary") or "",
        "ocr_text": _compact(str(ocr.get("full_text") or ""), 1200),
        "product_appearance": understanding.get("product_appearance") or [],
        "visible_accessories": understanding.get("visible_accessories") or [],
        "play_clues": understanding.get("play_clues") or [],
        "competitor_visual_hooks": understanding.get("competitor_visual_hooks") or [],
        "packaging_hierarchy": understanding.get("packaging_hierarchy") or [],
        "detail_page_sections": understanding.get("detail_page_sections") or [],
        "risks": understanding.get("risks") or analysis.get("warnings") or [],
    }


def _write_document_memory(
    *,
    project: ProjectRecord,
    asset: AssetRef,
    parsed: dict[str, Any],
    workflow_type: str | None,
) -> None:
    evidence = _document_evidence(asset, parsed)
    text = "\n".join(
        part
        for part in [
            f"Document: {asset.filename}",
            *evidence.get("highlights", []),
            evidence.get("text_excerpt", ""),
        ]
        if str(part).strip()
    )
    if not text.strip():
        return
    memory_type = "brand_vi" if asset.kind == "vi_document" else "product_doc"
    get_memory_store().upsert(
        MemoryUpsertRequest(
            text=text,
            memory_type=memory_type,  # type: ignore[arg-type]
            project_id=project.id,
            category=project.brief.category,
            workflow_type=workflow_type or project.workflow_type,  # type: ignore[arg-type]
            asset_id=asset.id,
            source_type="document_parse",
            metadata={"filename": asset.filename, "parser": parsed.get("parser")},
        )
    )


def _is_document_asset(asset: AssetRef) -> bool:
    suffix = Path(asset.filename).suffix.lower()
    mime = (asset.mime_type or "").lower()
    return suffix in {".ppt", ".pptx", ".pdf"} or "presentation" in mime or "pdf" in mime


def _evidence_digest(*, documents: list[dict[str, Any]], images: list[dict[str, Any]], missing: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for document in documents:
        lines.append(f"文档 {document.get('filename')}：{document.get('page_count')} 页，解析器 {document.get('parser')}")
        lines.extend(str(item) for item in document.get("highlights", [])[:4])
    for image in images:
        lines.append(f"图片 {image.get('filename')}：{image.get('summary')}")
        lines.extend(str(item) for item in (image.get("product_appearance") or [])[:4])
        lines.extend(str(item) for item in (image.get("play_clues") or [])[:4])
    if missing:
        lines.append("未完成解析/理解：" + "；".join(f"{item.get('filename')}({item.get('reason')})" for item in missing[:6]))
    return _compact("\n".join(item for item in lines if item.strip()), 16000)


def _compact(text: str, max_chars: int) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= max_chars:
        return compact
    return f"{compact[:max_chars].rstrip()}..."
