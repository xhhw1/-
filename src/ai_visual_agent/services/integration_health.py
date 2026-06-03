from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.util import find_spec
from typing import Any

from ai_visual_agent.config import Settings, get_settings
from ai_visual_agent.domain import IntegrationHealthItem, IntegrationHealthReport
from ai_visual_agent.services.persistence_config import (
    normalized_project_store_backend,
    project_store_uses_sql,
    resolved_project_database_url,
)


@dataclass
class IntegrationRuntimeEvent:
    name: str
    backend: str
    model: str | None
    ok: bool
    fallback_used: bool
    error: str | None
    checked_at: datetime


_RUNTIME_EVENTS: dict[str, IntegrationRuntimeEvent] = {}


def record_integration_event(
    *,
    name: str,
    backend: str,
    model: str | None = None,
    ok: bool = True,
    fallback_used: bool = False,
    error: str | None = None,
) -> None:
    _RUNTIME_EVENTS[name] = IntegrationRuntimeEvent(
        name=name,
        backend=backend,
        model=model,
        ok=ok,
        fallback_used=fallback_used,
        error=error,
        checked_at=datetime.now(UTC),
    )


def clear_integration_events() -> None:
    _RUNTIME_EVENTS.clear()


def build_integration_health_report(settings: Settings | Any | None = None) -> IntegrationHealthReport:
    settings = settings or get_settings()
    items = [
        _llm_health(settings),
        _multimodal_health(settings),
        _image_generation_health(settings),
        _document_parser_health(settings),
        _ocr_health(settings),
        _segmentation_health(settings),
        _persistence_health(settings),
        _memory_health(settings),
    ]
    warnings = [item.message for item in items if item.status in {"misconfigured", "degraded"}]
    if any(item.status == "misconfigured" for item in items):
        status = "misconfigured"
    elif any(item.status == "degraded" for item in items):
        status = "degraded"
    else:
        status = "ok"
    return IntegrationHealthReport(status=status, items=items, warnings=warnings)


def _llm_health(settings: Any) -> IntegrationHealthItem:
    backend = str(settings.llm_backend).lower()
    model = settings.deepseek_model_strategy if backend == "deepseek" else "mock-structured-llm"
    if backend == "deepseek":
        item = _env_item(
            name="llm",
            backend=backend,
            required={"DEEPSEEK_API_KEY": settings.deepseek_api_key},
            model=model,
            ready_message="DeepSeek structured LLM is configured.",
            missing_message="LLM_BACKEND=deepseek requires DEEPSEEK_API_KEY.",
        )
    else:
        item = IntegrationHealthItem(
            name="llm",
            backend=backend,
            status="mock",
            configured=True,
            ready=True,
            model=model,
            message="Structured LLM is using deterministic mock fallback.",
        )
    return _with_runtime_event(item)


def _multimodal_health(settings: Any) -> IntegrationHealthItem:
    backend = str(settings.multimodal_backend).lower()
    if backend == "gemini":
        item = _env_item(
            name="multimodal",
            backend=backend,
            required={"GEMINI_API_KEY": settings.gemini_api_key},
            model=settings.multimodal_model,
            ready_message="Gemini multimodal backend is configured.",
            missing_message="MULTIMODAL_BACKEND=gemini requires GEMINI_API_KEY.",
        )
    elif backend == "openai":
        item = _env_item(
            name="multimodal",
            backend=backend,
            required={"OPENAI_API_KEY": settings.openai_api_key},
            model=settings.multimodal_model,
            ready_message="OpenAI multimodal backend is configured.",
            missing_message="MULTIMODAL_BACKEND=openai requires OPENAI_API_KEY.",
        )
    elif backend in {"openai_compatible", "openai-compatible", "shiyun"}:
        api_key = (
            getattr(settings, "multimodal_api_key", None)
            or getattr(settings, "gemini_api_key", None)
            or getattr(settings, "openai_api_key", None)
        )
        base_url = getattr(settings, "multimodal_base_url", None) or getattr(
            settings, "openai_base_url", ""
        )
        item = _env_item(
            name="multimodal",
            backend=backend,
            required={"MULTIMODAL_API_KEY_OR_GEMINI_API_KEY": api_key, "MULTIMODAL_BASE_URL": base_url},
            model=settings.multimodal_model,
            ready_message="OpenAI-compatible multimodal backend is configured.",
            missing_message=(
                "MULTIMODAL_BACKEND=openai_compatible requires a multimodal key and "
                "MULTIMODAL_BASE_URL."
            ),
        )
    else:
        item = IntegrationHealthItem(
            name="multimodal",
            backend=backend,
            status="mock",
            configured=True,
            ready=True,
            model=settings.multimodal_model,
            message="Multimodal understanding is using mock backend.",
        )
    return _with_runtime_event(item)


def _image_generation_health(settings: Any) -> IntegrationHealthItem:
    backend = str(getattr(settings, "image_generation_backend", "auto")).lower()
    real_names = str(getattr(settings, "image_generation_real_names", "front"))
    effective_backend = "mock" if backend == "mock" or (backend == "auto" and settings.mock_external_tools) else "openai"
    if effective_backend == "mock":
        return IntegrationHealthItem(
            name="image_generation",
            backend="mock",
            status="mock",
            configured=True,
            ready=True,
            model=settings.openai_image_model,
            message="Image generation is using mock external tools.",
        )
    item = _env_item(
        name="image_generation",
        backend="openai",
        required={"OPENAI_API_KEY": settings.openai_api_key},
        model=settings.openai_image_model,
        ready_message=f"OpenAI image generation is configured for outputs: {real_names or 'none'}.",
        missing_message="Real image generation requires OPENAI_API_KEY.",
    )
    item.message = f"{item.message} Quality={getattr(settings, 'image_generation_quality', 'low')}."
    return item


def _document_parser_health(settings: Any) -> IntegrationHealthItem:
    backend = str(getattr(settings, "document_parser_backend", "local")).lower()
    if backend in {"llamaparse", "llama_parse", "llama-cloud", "llama_cloud"}:
        if find_spec("llama_cloud") is None:
            return IntegrationHealthItem(
                name="document_parser",
                backend=backend,
                status="misconfigured",
                configured=False,
                ready=False,
                required_env=["LLAMA_CLOUD_API_KEY"],
                message="DOCUMENT_PARSER_BACKEND=llamaparse requires the llama-cloud package.",
            )
        return _env_item(
            name="document_parser",
            backend="llamaparse",
            required={"LLAMA_CLOUD_API_KEY": getattr(settings, "llama_cloud_api_key", None)},
            model=f"{getattr(settings, 'llama_parse_tier', 'fast')}:{getattr(settings, 'llama_parse_version', 'latest')}",
            ready_message="LlamaParse document parser is configured.",
            missing_message="DOCUMENT_PARSER_BACKEND=llamaparse requires LLAMA_CLOUD_API_KEY.",
        )

    missing_packages = [
        package
        for package, module in {"python-pptx": "pptx", "pymupdf": "fitz"}.items()
        if find_spec(module) is None
    ]
    if missing_packages:
        return IntegrationHealthItem(
            name="document_parser",
            backend="local",
            status="misconfigured",
            configured=False,
            ready=False,
            message=f"Local document parsing requires: {', '.join(missing_packages)}.",
        )
    return IntegrationHealthItem(
        name="document_parser",
        backend="local",
        status="ready",
        configured=True,
        ready=True,
        message="Local PPTX/PDF parsing is available.",
    )


def _ocr_health(settings: Any) -> IntegrationHealthItem:
    backend = str(settings.ocr_backend).lower()
    if backend in {"paddle", "paddleocr"} and find_spec("paddleocr") is None:
        return IntegrationHealthItem(
            name="ocr",
            backend=backend,
            status="misconfigured",
            configured=False,
            ready=False,
            message="OCR_BACKEND=paddle requires paddleocr to be installed.",
        )
    return IntegrationHealthItem(
        name="ocr",
        backend=backend,
        status="mock" if backend == "mock" else "ready",
        configured=True,
        ready=True,
        message="OCR backend is available." if backend != "mock" else "OCR is using mock backend.",
    )


def _segmentation_health(settings: Any) -> IntegrationHealthItem:
    backend = str(settings.segmentation_backend).lower()
    if backend == "sam2":
        missing = [
            name
            for name, value in {
                "SAM2_CHECKPOINT": settings.sam2_checkpoint,
                "SAM2_MODEL_CFG": settings.sam2_model_cfg,
            }.items()
            if not value
        ]
        if missing:
            return IntegrationHealthItem(
                name="segmentation",
                backend=backend,
                status="misconfigured",
                configured=False,
                ready=False,
                required_env=["SAM2_CHECKPOINT", "SAM2_MODEL_CFG"],
                missing_env=missing,
                message="SEGMENTATION_BACKEND=sam2 requires SAM2_CHECKPOINT and SAM2_MODEL_CFG.",
            )
    return IntegrationHealthItem(
        name="segmentation",
        backend=backend,
        status="mock" if backend == "mock" else "ready",
        configured=True,
        ready=True,
        message="Segmentation backend is available." if backend != "mock" else "Segmentation is using mock backend.",
    )


def _persistence_health(settings: Any) -> IntegrationHealthItem:
    backend = normalized_project_store_backend(settings)
    uses_graph_postgres = str(
        settings.graph_checkpoint_backend
    ).lower() in {"postgres", "postgresql", "sql"}
    uses_sql = project_store_uses_sql(settings) or uses_graph_postgres
    database_url = resolved_project_database_url(settings)
    if uses_sql and not database_url:
        return IntegrationHealthItem(
            name="persistence",
            backend=backend,
            status="misconfigured",
            configured=False,
            ready=False,
            required_env=["DATABASE_URL or LOCAL_DATABASE_URL"],
            missing_env=["DATABASE_URL or LOCAL_DATABASE_URL"],
            message="SQL persistence requires DATABASE_URL or LOCAL_DATABASE_URL.",
        )
    if uses_sql:
        message = (
            "SQLite local persistence is configured."
            if backend == "sqlite"
            else "PostgreSQL persistence is configured."
        )
        return IntegrationHealthItem(
            name="persistence",
            backend=backend,
            status="ready",
            configured=True,
            ready=True,
            message=message,
        )
    return IntegrationHealthItem(
        name="persistence",
        backend=backend,
        status="mock",
        configured=True,
        ready=True,
        message="Persistence is using in-memory store.",
    )


def _memory_health(settings: Any) -> IntegrationHealthItem:
    if settings.mock_external_tools:
        return IntegrationHealthItem(
            name="memory",
            backend="memory",
            status="mock",
            configured=True,
            ready=True,
            message="Semantic memory is using in-memory fallback.",
        )
    if not settings.qdrant_url:
        return IntegrationHealthItem(
            name="memory",
            backend="qdrant",
            status="misconfigured",
            configured=False,
            ready=False,
            required_env=["QDRANT_URL"],
            missing_env=["QDRANT_URL"],
            message="Qdrant memory requires QDRANT_URL.",
        )
    return IntegrationHealthItem(
        name="memory",
        backend="qdrant",
        status="ready",
        configured=True,
        ready=True,
        message="Qdrant memory is configured.",
    )


def _env_item(
    *,
    name: str,
    backend: str,
    required: dict[str, Any],
    model: str | None,
    ready_message: str,
    missing_message: str,
) -> IntegrationHealthItem:
    missing = [key for key, value in required.items() if not value]
    if missing:
        return IntegrationHealthItem(
            name=name,
            backend=backend,
            status="misconfigured",
            configured=False,
            ready=False,
            model=model,
            required_env=list(required.keys()),
            missing_env=missing,
            message=missing_message,
        )
    return IntegrationHealthItem(
        name=name,
        backend=backend,
        status="ready",
        configured=True,
        ready=True,
        model=model,
        required_env=list(required.keys()),
        message=ready_message,
    )


def _with_runtime_event(item: IntegrationHealthItem) -> IntegrationHealthItem:
    event = _RUNTIME_EVENTS.get(item.name)
    if not event:
        return item

    item.last_checked_at = event.checked_at
    item.last_error = event.error
    item.fallback_used = event.fallback_used
    item.model = event.model or item.model
    if item.status != "misconfigured" and not event.ok:
        item.status = "degraded"
        item.ready = False
        item.message = event.error or f"{item.name} backend failed and used fallback."
    return item
