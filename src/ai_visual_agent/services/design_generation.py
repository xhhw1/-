from __future__ import annotations

import base64
import struct
import time
import zlib
from dataclasses import dataclass, field
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import AssetRef, GenerationOutput, GenerationOutputItem
from ai_visual_agent.services.audit_store import audit_store
from ai_visual_agent.services.project_store import project_store
from ai_visual_agent.services.rate_limiter import image_generation_budget
from ai_visual_agent.services.storage import asset_storage


@dataclass(frozen=True)
class DesignGenerationJob:
    project_id: str
    workflow_type: str
    name: str
    prompt: str
    layout_spec: dict[str, Any]
    reference_asset_ids: list[str] = field(default_factory=list)
    vi_profile: dict[str, Any] = field(default_factory=dict)
    revision_round: int = 0


class ImageGenerationProvider(Protocol):
    engine_name: str

    def generate_base(self, job: DesignGenerationJob) -> AssetRef: ...


class LayoutComposer(Protocol):
    engine_name: str

    def compose(self, job: DesignGenerationJob, base_asset: AssetRef) -> AssetRef: ...


class MockImageGenerationProvider:
    engine_name = "mock-gpt-image-2"

    def generate_base(self, job: DesignGenerationJob) -> AssetRef:
        width, height = _dimensions_for(job)
        image = _make_mock_png(
            width=width,
            height=height,
            seed=f"{job.workflow_type}:{job.name}:base:{job.prompt}",
            accent="visual_base",
        )
        return asset_storage.save_bytes(
            project_id=job.project_id,
            kind="other",
            filename=f"{job.workflow_type}_{job.name}_base_r{job.revision_round}.png",
            content=image,
            mime_type="image/png",
            metadata={
                "asset_role": "generated_visual_base",
                "engine": self.engine_name,
                "prompt": job.prompt,
                "reference_asset_ids": job.reference_asset_ids,
                "actual_reference_asset_ids": job.reference_asset_ids,
                "revision_round": job.revision_round,
            },
        )


class OpenAIImageGenerationProvider:
    engine_name = "openai-image"

    def generate_base(self, job: DesignGenerationJob) -> AssetRef:
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for real image generation.")

        client = _create_openai_image_client(settings)
        prompt = _build_image_prompt(job)
        size = _openai_size_for(job)
        actual_reference_asset_ids = _actual_reference_asset_ids_for_request(
            settings=settings,
            job=job,
            model=settings.openai_image_model,
        )
        with image_generation_budget(identity=job.project_id):
            response = _generate_with_reference_if_available(
                settings=settings,
                client=client,
                job=job,
                prompt=prompt,
                model=settings.openai_image_model,
                size=size,
                quality=settings.image_generation_quality,
            )
        image = _image_bytes_from_response(response)
        return asset_storage.save_bytes(
            project_id=job.project_id,
            kind="other",
            filename=f"{job.workflow_type}_{job.name}_base_r{job.revision_round}.png",
            content=image,
            mime_type="image/png",
            metadata={
                "asset_role": "generated_visual_base",
                "engine": self.engine_name,
                "model": settings.openai_image_model,
                "base_url": settings.openai_base_url,
                "prompt": prompt,
                "reference_asset_ids": job.reference_asset_ids,
                "actual_reference_asset_ids": actual_reference_asset_ids,
                "revision_round": job.revision_round,
                "size": size,
            },
        )


class SelectiveImageGenerationProvider:
    engine_name = "selective-image-generation"

    def __init__(
        self,
        *,
        real_provider: ImageGenerationProvider | None = None,
        mock_provider: ImageGenerationProvider | None = None,
        real_names: str = "front",
    ) -> None:
        self.real_provider = real_provider or OpenAIImageGenerationProvider()
        self.mock_provider = mock_provider or MockImageGenerationProvider()
        self.real_names = real_names

    def generate_base(self, job: DesignGenerationJob) -> AssetRef:
        if _should_use_real_image_generation(job.name, self.real_names):
            return self.real_provider.generate_base(job)
        return self.mock_provider.generate_base(job)


class MockLayoutComposer:
    engine_name = "mock-layout-composer"

    def compose(self, job: DesignGenerationJob, base_asset: AssetRef) -> AssetRef:
        width, height = _dimensions_for(job)
        image = _make_mock_png(
            width=width,
            height=height,
            seed=f"{job.workflow_type}:{job.name}:layout:{job.prompt}:{base_asset.id}",
            accent="layout_composed",
        )
        return asset_storage.save_bytes(
            project_id=job.project_id,
            kind="other",
            filename=f"{job.workflow_type}_{job.name}_composed_r{job.revision_round}.png",
            content=image,
            mime_type="image/png",
            metadata={
                "asset_role": "composed_design_output",
                "engine": self.engine_name,
                "base_asset_id": base_asset.id,
                "prompt": job.prompt,
                "layout_spec": job.layout_spec,
                "reference_asset_ids": job.reference_asset_ids,
                "actual_reference_asset_ids": base_asset.metadata.get("actual_reference_asset_ids", job.reference_asset_ids),
                "vi_profile": job.vi_profile,
                "revision_round": job.revision_round,
            },
        )


def generate_design_outputs(
    *,
    project_id: str,
    workflow_type: str,
    strategy: dict[str, Any],
    vi_profile: dict[str, Any],
    revision_round: int,
    reference_asset_ids: list[str],
    main_image_prompt_draft: dict[str, Any] | None = None,
    allow_mock_fallback: bool = True,
    return_partial_on_error: bool = False,
    on_item_generated: Any | None = None,
    on_generation_error: Any | None = None,
) -> GenerationOutput:
    jobs = _jobs_for_strategy(
        project_id=project_id,
        workflow_type=workflow_type,
        strategy=strategy,
        vi_profile=vi_profile,
        revision_round=revision_round,
        reference_asset_ids=reference_asset_ids,
        main_image_prompt_draft=main_image_prompt_draft,
    )
    image_provider = get_image_generation_provider()
    items: list[GenerationOutputItem] = []

    for job in jobs:
        try:
            item = _generate_design_item(
                job=job,
                image_provider=image_provider,
                allow_mock_fallback=allow_mock_fallback,
            )
        except Exception as exc:
            error_payload = {
                "name": job.name,
                "error": f"{type(exc).__name__}: {exc}",
                "reference_asset_ids": job.reference_asset_ids,
                "full_image_prompt": _build_image_prompt(job),
            }
            if on_generation_error:
                on_generation_error(job, error_payload, list(items), len(jobs))
            if return_partial_on_error:
                break
            raise
        items.append(item)
        if on_item_generated:
            on_item_generated(item, list(items), len(jobs))

    output = GenerationOutput(items=items, revision_round=revision_round)
    audit_store.record(
        project_id=project_id,
        record_type="agent_output",
        stage="generate_design_assets",
        payload={
            "workflow_type": workflow_type,
            "revision_round": revision_round,
            "items": [item.model_dump() for item in items],
        },
    )
    return output


def _generate_design_item(
    *,
    job: DesignGenerationJob,
    image_provider: ImageGenerationProvider,
    allow_mock_fallback: bool,
) -> GenerationOutputItem:
    full_image_prompt = _build_image_prompt(job)
    image_generation_error = ""
    image_generation_fallback_used = False
    try:
        base_asset = image_provider.generate_base(job)
        image_engine = str(base_asset.metadata.get("engine") or image_provider.engine_name)
        _register_generated_asset(job.project_id, base_asset)
    except Exception as exc:
        if not allow_mock_fallback:
            raise RuntimeError(f"Image generation failed for {job.name}: {type(exc).__name__}: {exc}") from exc
        image_generation_error = f"{type(exc).__name__}: {exc}"
        image_generation_fallback_used = True
        fallback_provider = MockImageGenerationProvider()
        base_asset = fallback_provider.generate_base(job)
        _register_generated_asset(job.project_id, base_asset)
        image_engine = f"{image_provider.engine_name}->{fallback_provider.engine_name}"
    return GenerationOutputItem(
        name=job.name,
        asset_id=base_asset.id,
        uri=base_asset.uri,
        prompt=job.prompt,
        layout_spec={
            **job.layout_spec,
            "base_asset_id": base_asset.id,
            "base_asset_uri": base_asset.uri,
            "image_engine": image_engine,
            "layout_engine": "disabled",
            "revision_round": job.revision_round,
            "full_image_prompt": full_image_prompt,
            "reference_asset_ids": job.reference_asset_ids,
            "actual_reference_asset_ids": base_asset.metadata.get("actual_reference_asset_ids", job.reference_asset_ids),
            "image_generation_fallback_used": image_generation_fallback_used,
            "image_generation_error": image_generation_error,
        },
    )


def _register_generated_asset(project_id: str, asset: AssetRef) -> None:
    try:
        project = project_store.get(project_id)
    except KeyError:
        return
    if any(existing.id == asset.id for existing in project.assets):
        return
    try:
        project_store.add_asset(project_id, asset)
    except KeyError:
        return


def _jobs_for_strategy(
    *,
    project_id: str,
    workflow_type: str,
    strategy: dict[str, Any],
    vi_profile: dict[str, Any],
    revision_round: int,
    reference_asset_ids: list[str],
    main_image_prompt_draft: dict[str, Any] | None = None,
) -> list[DesignGenerationJob]:
    if workflow_type == "packaging":
        if main_image_prompt_draft:
            prompt = str(
                main_image_prompt_draft.get("main_image_prompt")
                or strategy.get("front_layout")
                or ""
            )
            return [
                DesignGenerationJob(
                    project_id=project_id,
                    workflow_type=workflow_type,
                    name="front",
                    prompt=prompt,
                    layout_spec={
                        "surface": "front",
                        "workflow_type": workflow_type,
                        "product_name": strategy.get("product_name", ""),
                        "required_copy": strategy.get("required_copy", []),
                        "required_icons": strategy.get("required_icons", []),
                        "tone": strategy.get("overall_tone", ""),
                        "prebuilt_image_prompt": prompt,
                        "negative_prompt": main_image_prompt_draft.get("negative_prompt", ""),
                        "reference_usage": main_image_prompt_draft.get("reference_usage", ""),
                        "layout_notes": main_image_prompt_draft.get("layout_notes", ""),
                        "text_overlay_plan": main_image_prompt_draft.get("text_overlay_plan", []),
                        "prompt_risk_notes": main_image_prompt_draft.get("risk_notes", []),
                    },
                    reference_asset_ids=reference_asset_ids,
                    vi_profile=vi_profile,
                    revision_round=revision_round,
                )
            ]
        face_fields = [
            ("front", "front_layout"),
            ("left", "left_layout"),
            ("right", "right_layout"),
            ("back", "back_layout"),
        ]
        return [
            DesignGenerationJob(
                project_id=project_id,
                workflow_type=workflow_type,
                name=face,
                prompt=str(strategy.get(field) or strategy.get("front_layout") or ""),
                layout_spec={
                    "surface": face,
                    "workflow_type": workflow_type,
                    "product_name": strategy.get("product_name", ""),
                    "required_copy": strategy.get("required_copy", []),
                    "required_icons": strategy.get("required_icons", []),
                    "tone": strategy.get("overall_tone", ""),
                },
                reference_asset_ids=reference_asset_ids,
                vi_profile=vi_profile,
                revision_round=revision_round,
            )
            for face, field in face_fields
        ]

    screens = strategy.get("screens", [])
    return [
        DesignGenerationJob(
            project_id=project_id,
            workflow_type=workflow_type,
            name=f"screen_{screen.get('screen_index')}",
            prompt=" ".join(
                str(part)
                for part in [screen.get("goal"), screen.get("visual"), screen.get("copy_text")]
                if part
            ),
            layout_spec={
                "screen_index": screen.get("screen_index"),
                "workflow_type": workflow_type,
                "goal": screen.get("goal", ""),
                "copy_text": screen.get("copy_text", ""),
                "product_angle": screen.get("product_angle", ""),
                "proof_points": screen.get("proof_points", []),
            },
            reference_asset_ids=reference_asset_ids,
            vi_profile=vi_profile,
            revision_round=revision_round,
        )
        for screen in screens
    ]


@lru_cache
def get_image_generation_provider() -> ImageGenerationProvider:
    settings = get_settings()
    backend = settings.image_generation_backend.lower()
    if backend == "mock" or (backend == "auto" and settings.mock_external_tools):
        return MockImageGenerationProvider()
    if backend in {"openai", "real"} or backend == "auto":
        return SelectiveImageGenerationProvider(real_names=settings.image_generation_real_names)
    return OpenAIImageGenerationProvider()


@lru_cache
def get_layout_composer() -> LayoutComposer:
    return MockLayoutComposer()


def _create_openai_image_client(settings: Any) -> Any:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Real image generation requires the openai package.") from exc

    return OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)


def _generate_with_reference_if_available(
    *,
    settings: Any,
    client: Any,
    job: DesignGenerationJob,
    prompt: str,
    model: str,
    size: str,
    quality: str,
) -> Any:
    reference_images = _reference_image_assets(job)
    if job.reference_asset_ids and not reference_images:
        raise RuntimeError(
            "Reference asset ids were provided, but no local image files could be resolved. "
            "Please re-upload the product reference image before generating."
        )
    if reference_images:
        if _uses_shiyun_generation_reference(settings):
            reference_limit = 4 if _supports_shiyun_multi_reference(model) else 1
            return _generate_shiyun_with_reference_image(
                settings=settings,
                reference_paths=[path for _, path in reference_images[:reference_limit]],
                prompt=prompt,
                model=model,
                size=size,
                quality=quality,
            )
        try:
            with reference_images[0][1].open("rb") as image_file:
                def edit_with_reference() -> Any:
                    image_file.seek(0)
                    return client.images.edit(
                        model=model,
                        image=image_file,
                        prompt=prompt,
                        size=size,
                        quality=quality,
                        output_format="png",
                    )

                return _call_image_api_with_retries(
                    edit_with_reference
                )
        except Exception as exc:
            raise RuntimeError(
                "Image edit with product reference failed after retries; "
                "refusing to generate without the provided product image."
            ) from exc

    return _call_image_api_with_retries(
        lambda: client.images.generate(
            model=model,
            prompt=prompt,
            size=size,
            quality=quality,
            output_format="png",
        )
    )


def _uses_shiyun_generation_reference(settings: Any) -> bool:
    base_url = str(getattr(settings, "openai_base_url", "") or "").lower()
    return "shiyunapi.com" in base_url


def _generate_shiyun_with_reference_image(
    *,
    settings: Any,
    reference_paths: list[Path],
    prompt: str,
    model: str,
    size: str,
    quality: str,
) -> Any:
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("Shiyun image generation requires httpx.") from exc

    image_data_urls = [_image_data_url_for_reference(path) for path in reference_paths[:4]]
    image_payload: str | list[str] = (
        image_data_urls if _supports_shiyun_multi_reference(model) else image_data_urls[0]
    )
    endpoint = str(settings.openai_base_url).rstrip("/") + "/images/generations"
    response = _post_image_generation_with_retries(
        httpx_module=httpx,
        endpoint=endpoint,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {settings.openai_api_key}",
        },
        body={
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "quality": quality,
            "image": image_payload,
        },
        timeout=float(getattr(settings, "image_generation_timeout", 420.0)),
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = response.text[:1200]
        reference_count = len(image_data_urls)
        raise RuntimeError(
            f"Shiyun image generation failed: status={response.status_code}, "
            f"model={model}, references={reference_count}, body={body}"
        ) from exc
    return response.json()


def _post_image_generation_with_retries(
    *,
    httpx_module: Any,
    endpoint: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
    attempts: int = 5,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = httpx_module.post(endpoint, headers=headers, json=body, timeout=timeout)
        except (httpx_module.TimeoutException, httpx_module.TransportError) as exc:
            last_error = exc
            if attempt >= attempts:
                raise RuntimeError(_image_api_retry_exhausted_message(exc, attempts)) from exc
            time.sleep(min(2 ** (attempt - 1), 4))
            continue
        status_code = int(getattr(response, "status_code", 200) or 200)
        if status_code not in {408, 409, 425, 429} and status_code < 500:
            return response
        if attempt >= attempts:
            return response
        time.sleep(min(2 ** (attempt - 1), 4))
    if last_error:
        raise RuntimeError(_image_api_retry_exhausted_message(last_error, attempts)) from last_error
    raise RuntimeError("Image generation request did not return a response.")


def _call_image_api_with_retries(operation: Any, attempts: int = 4) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if not _is_retryable_image_api_error(exc):
                raise
            last_error = exc
            if attempt >= attempts:
                raise RuntimeError(_image_api_retry_exhausted_message(exc, attempts)) from exc
            time.sleep(min(2 ** (attempt - 1), 4))
    if last_error:
        raise RuntimeError(_image_api_retry_exhausted_message(last_error, attempts)) from last_error
    raise RuntimeError("Image generation request did not return a response.")


def _is_retryable_image_api_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    retryable_names = (
        "apiconnectionerror",
        "apitimeouterror",
        "connecterror",
        "connectionerror",
        "readerror",
        "timeout",
        "sslerror",
        "transporterror",
    )
    retryable_messages = (
        "unexpected_eof",
        "eof occurred",
        "connection reset",
        "server disconnected",
        "remote protocol",
        "temporarily unavailable",
        "timeout",
    )
    return any(item in name for item in retryable_names) or any(item in message for item in retryable_messages)


def _image_api_retry_exhausted_message(exc: Exception, attempts: int) -> str:
    return (
        f"Image API network request failed after {attempts} attempts. "
        f"This is usually a transient SSL/connection interruption; please retry generation. "
        f"Last error: {type(exc).__name__}: {exc}"
    )


def _supports_shiyun_multi_reference(model: str) -> bool:
    return str(model).lower().endswith("-all")


def _image_data_url_for_reference(path: Path) -> str:
    mime_type, image_bytes = _prepared_reference_image(path)
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{image_b64}"


def _prepared_reference_image(path: Path) -> tuple[str, bytes]:
    original_mime = _mime_type_for_reference(path)
    original_bytes = path.read_bytes()
    try:
        from PIL import Image
    except ImportError:
        return original_mime, original_bytes

    try:
        with Image.open(path) as image:
            image.thumbnail((1536, 1536))
            buffer = BytesIO()
            has_alpha = image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            )
            if has_alpha:
                image.save(buffer, format="PNG", optimize=True)
                return "image/png", buffer.getvalue()
            image.convert("RGB").save(buffer, format="JPEG", quality=88, optimize=True)
            return "image/jpeg", buffer.getvalue()
    except Exception:
        return original_mime, original_bytes


def _mime_type_for_reference(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


def _reference_image_paths(job: DesignGenerationJob) -> list[Path]:
    return [path for _, path in _reference_image_assets(job)]


def _reference_image_assets(job: DesignGenerationJob) -> list[tuple[str, Path]]:
    if not job.reference_asset_ids:
        return []
    try:
        project = project_store.get(job.project_id)
    except KeyError:
        return []
    by_id = {
        asset.id: asset
        for asset in project.assets
        if (asset.mime_type or "").startswith("image/")
    }
    ordered_paths: list[tuple[str, Path]] = []
    for asset_id in job.reference_asset_ids:
        asset = by_id.get(asset_id)
        if not asset:
            continue
        path = Path(asset.uri)
        if path.exists() and path.is_file():
            ordered_paths.append((asset_id, path))
    return ordered_paths


def _actual_reference_asset_ids_for_request(*, settings: Any, job: DesignGenerationJob, model: str) -> list[str]:
    reference_images = _reference_image_assets(job)
    if not reference_images:
        return []
    if _uses_shiyun_generation_reference(settings):
        reference_limit = 4 if _supports_shiyun_multi_reference(model) else 1
        return [asset_id for asset_id, _ in reference_images[:reference_limit]]
    return [reference_images[0][0]]


def _build_image_prompt(job: DesignGenerationJob) -> str:
    if job.layout_spec.get("prebuilt_image_prompt"):
        return "\n".join(
            part
            for part in [
                str(job.layout_spec.get("prebuilt_image_prompt") or ""),
                f"负向约束：{job.layout_spec.get('negative_prompt')}" if job.layout_spec.get("negative_prompt") else "",
                f"参考图使用：{job.layout_spec.get('reference_usage')}" if job.layout_spec.get("reference_usage") else "",
                "包装正面主图只允许一处卖点文字表达区；不要同时生成卖点副标题、底部卖点条、多个卖点徽章或带卖点文案的功能图标。",
                "可以生成已上传 LOGO、品名、年龄角标、系列编号、辅助产品图和装饰性标签，使画面接近完整包装设计稿。",
                "正面主图不要生成认证标识区、安全提示区、条码占位区或制造商信息区；这些内容属于背面包装信息。",
            ]
            if str(part).strip()
        )
    raw_required_copy = job.layout_spec.get("required_copy") or []
    if not isinstance(raw_required_copy, list):
        raw_required_copy = [raw_required_copy]
    copy_guard = ", ".join(str(item) for item in raw_required_copy[:1] if item)
    icon_guard = ", ".join(str(item) for item in job.layout_spec.get("required_icons", []) if item)
    colors = ", ".join(str(item) for item in job.vi_profile.get("brand_colors", []) if item)
    logo_note = (
        f"LOGO参考素材ID：{job.vi_profile.get('logo_asset_id')}"
        if job.vi_profile.get("logo_asset_id")
        else "未提供品牌LOGO时，可以设计品牌识别区域和装饰性标志位，但不要虚构具体品牌LOGO。"
    )
    vi_rules = "；".join(str(item) for item in job.vi_profile.get("layout_rules", []) if item)
    forbidden = "；".join(str(item) for item in job.vi_profile.get("forbidden_rules", []) if item)
    return "\n".join(
        part
        for part in [
            "你是电商视觉设计出图引擎，请基于产品参考图生成高端商业视觉底图。",
            f"工作流：{job.workflow_type}；画面/包装面：{job.name}。",
            f"设计方向：{job.prompt}",
            f"整体影调：{job.layout_spec.get('tone', '')}",
            f"产品名称上下文：{job.layout_spec.get('product_name', '')}",
            f"品牌色/VI色彩：{colors}" if colors else "品牌色/VI色彩：未提供明确VI色，使用温和、干净、适合婴童电商的配色。",
            logo_note,
            f"正面主图只生成这一处已确认中文卖点短文案：{copy_guard}" if copy_guard else "可生成一处适合包装正面的中文主卖点短标题。",
            f"可设计这些标识/图标：{icon_guard}" if icon_guard else "",
            f"VI版式规则：{vi_rules}" if vi_rules else "",
            f"禁止事项：{forbidden}" if forbidden else "",
            "必须严格保持参考图中产品的形态、颜色、配件数量、吸盘/旋转部件和核心结构，不要添加不存在的部件。",
            "只保留一处卖点文字表达；辅助图和功能图标用图形表达，不重复写卖点。正面主图不要出现认证标识区、安全提示区、条码占位区或制造商信息区。",
        ]
        if str(part).strip()
    )


def _image_bytes_from_response(response: Any) -> bytes:
    data = _object_get(response, "data") or []
    first = data[0] if data else {}
    b64_json = _object_get(first, "b64_json")
    if b64_json:
        return base64.b64decode(str(b64_json))
    url = _object_get(first, "url")
    if url:
        return _download_image_url(str(url))
    raise RuntimeError("Image generation response did not include b64_json or url.")


def _download_image_url(url: str) -> bytes:
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("Downloading generated image URLs requires httpx.") from exc

    response = httpx.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def _object_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _should_use_real_image_generation(name: str, real_names: str) -> bool:
    selected = {item.strip().lower() for item in real_names.split(",") if item.strip()}
    if not selected:
        return False
    return "*" in selected or name.lower() in selected


def _openai_size_for(job: DesignGenerationJob) -> str:
    width, height = _dimensions_for(job)
    if width == height:
        return "1024x1024"
    if height > width:
        return "1024x1536"
    return "1536x1024"


def _dimensions_for(job: DesignGenerationJob) -> tuple[int, int]:
    if job.workflow_type == "detail_page":
        return 540, 720
    if job.name in {"left", "right"}:
        return 420, 700
    if job.name == "back":
        return 560, 700
    return 560, 560


def _make_mock_png(*, width: int, height: int, seed: str, accent: str) -> bytes:
    palette = _palette(seed)
    rows = []
    border = max(8, min(width, height) // 48)
    band_h = max(1, height // 7)
    band_w = max(1, width // 9)
    for y in range(height):
        row = bytearray()
        for x in range(width):
            if x < border or y < border or x >= width - border or y >= height - border:
                color = palette[2]
            elif (x // band_w + y // band_h) % 5 == 0:
                color = palette[1]
            else:
                shade = (x * 3 + y * 5 + len(accent) * 17) % 28
                color = tuple(max(0, min(255, channel + shade)) for channel in palette[0])
            row.extend(color)
        rows.append(b"\x00" + bytes(row))
    raw = b"".join(rows)
    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            _png_chunk(b"IDAT", zlib.compress(raw, level=6)),
            _png_chunk(b"IEND", b""),
        ]
    )


def _palette(seed: str) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    digest = zlib.crc32(seed.encode("utf-8"))
    base = (
        210 + digest % 30,
        210 + (digest >> 8) % 32,
        215 + (digest >> 16) % 28,
    )
    accent = (
        60 + (digest >> 4) % 140,
        70 + (digest >> 12) % 120,
        80 + (digest >> 20) % 110,
    )
    border = (
        30 + (digest >> 6) % 80,
        35 + (digest >> 14) % 75,
        45 + (digest >> 22) % 70,
    )
    return base, accent, border


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type)
    checksum = zlib.crc32(data, checksum)
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", checksum & 0xFFFFFFFF)
