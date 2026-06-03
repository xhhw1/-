from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from ai_visual_agent.api.auth_routes import auth_router
from ai_visual_agent.api.routes import router
from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import IntegrationHealthReport
from ai_visual_agent.services.integration_health import build_integration_health_report


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    from ai_visual_agent.services.task_queue import background_task_queue

    background_task_queue.recover_interrupted_jobs()
    yield
    from ai_visual_agent.services.workflow_engine import workflow_engine

    workflow_engine.close()


def create_app() -> FastAPI:
    settings = get_settings()
    web_dir = Path(__file__).resolve().parent / "web"
    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    app = FastAPI(
        title="AI Visual Agent",
        version="0.1.0",
        description="LangGraph + LangChain multi-agent workflow for ecommerce visual design.",
        lifespan=lifespan,
    )

    @app.get("/health")
    def health() -> dict[str, str | bool | int]:
        integration_report = build_integration_health_report(settings)
        return {
            "status": "ok",
            "app_env": settings.app_env,
            "mock_external_tools": settings.mock_external_tools,
            "project_store_backend": settings.project_store_backend,
            "graph_checkpoint_backend": settings.graph_checkpoint_backend,
            "document_parser_backend": settings.document_parser_backend,
            "auto_analyze_images": settings.auto_analyze_images,
            "ocr_backend": settings.ocr_backend,
            "ocr_language": settings.ocr_language,
            "segmentation_backend": settings.segmentation_backend,
            "multimodal_backend": settings.multimodal_backend,
            "multimodal_model": settings.multimodal_model,
            "llm_backend": settings.llm_backend,
            "deepseek_model_strategy": settings.deepseek_model_strategy,
            "image_generation_backend": settings.image_generation_backend,
            "image_generation_real_names": settings.image_generation_real_names,
            "integration_status": integration_report.status,
            "integration_warnings": len(integration_report.warnings),
            "qdrant_collection": settings.qdrant_collection,
            "memory_embedding_dim": settings.memory_embedding_dim,
            "auth_enabled": settings.auth_enabled,
            "admin_email": settings.admin_email,
            "task_queue_backend": settings.task_queue_backend,
            "task_queue_redis_queue_name": settings.task_queue_redis_queue_name,
            "rate_limit_enabled": settings.rate_limit_enabled,
            "rate_limit_backend": settings.rate_limit_backend,
            "image_generation_max_concurrent": settings.image_generation_max_concurrent,
        }

    @app.get("/health/integrations", response_model=IntegrationHealthReport)
    def integration_health() -> IntegrationHealthReport:
        return build_integration_health_report(settings)

    app.include_router(auth_router)
    app.include_router(router)
    app.mount("/app", StaticFiles(directory=web_dir, html=True), name="review_console")
    if frontend_dist.exists():
        app.mount(
            "/app-next",
            StaticFiles(directory=frontend_dist, html=True),
            name="production_console",
        )

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/app/")

    return app


app = create_app()
