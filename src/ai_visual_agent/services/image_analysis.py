from pathlib import Path

from ai_visual_agent.domain import AssetRef, ImageAssetAnalysis, ImageUnderstandingResult, MemoryUpsertRequest
from ai_visual_agent.services.audit_store import audit_store
from ai_visual_agent.services.image_understanding import get_image_understanding_provider
from ai_visual_agent.services.memory_store import get_memory_store
from ai_visual_agent.services.storage import asset_storage
from ai_visual_agent.tools.vision_tools import ocr_image_file


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def is_image_asset(asset: AssetRef) -> bool:
    mime = (asset.mime_type or "").lower()
    suffix = Path(asset.filename).suffix.lower()
    return mime.startswith("image/") or suffix in IMAGE_EXTENSIONS


def classify_image_role(asset: AssetRef) -> str:
    filename = asset.filename.lower()
    kind = asset.kind
    if kind == "product_image":
        return "product_image"
    if kind == "competitor_packaging":
        return "competitor_packaging"
    if kind == "competitor_detail_page":
        return "competitor_detail_page"
    if kind == "competitor_image":
        return "competitor_image"
    if kind == "logo":
        return "logo"
    if kind == "vi_document":
        return "vi_reference_image"
    if "logo" in filename:
        return "logo"
    if "detail" in filename or "详情" in filename:
        return "detail_page_image"
    if "pack" in filename or "包装" in filename:
        return "packaging_image"
    return "other_image"


def _read_image_size(uri: str) -> tuple[int | None, int | None]:
    try:
        from PIL import Image
    except ImportError:
        return None, None

    try:
        with Image.open(uri) as image:
            return int(image.width), int(image.height)
    except Exception:
        return None, None


def _fallback_understanding(asset: AssetRef, image_role: str, error: Exception) -> ImageUnderstandingResult:
    return ImageUnderstandingResult(
        image_id=asset.id,
        image_uri=asset.uri,
        engine="unavailable-vlm",
        image_role=image_role,
        summary="Image understanding failed. Check warnings and backend configuration.",
        risks=[f"{type(error).__name__}: {error}"],
        tags=[image_role, "visual_understanding_failed"],
    )


def _memory_type_for_image_role(image_role: str) -> str:
    if image_role in {"logo", "vi_reference_image", "packaging_image"}:
        return "brand_vi"
    if image_role.startswith("competitor"):
        return "competitor"
    return "product_doc"


def _understanding_memory_text(understanding: ImageUnderstandingResult) -> str:
    sections = [
        understanding.summary,
        *understanding.product_appearance,
        *understanding.visible_accessories,
        *understanding.play_clues,
        *understanding.competitor_visual_hooks,
        *understanding.packaging_hierarchy,
        *understanding.detail_page_sections,
    ]
    return "\n".join(item for item in sections if item.strip())


def analyze_image_asset(
    project_id: str,
    asset: AssetRef,
    workflow_type: str | None = None,
    category: str | None = None,
) -> ImageAssetAnalysis:
    if not is_image_asset(asset):
        raise ValueError(f"Asset is not an image: {asset.filename}")

    image_path = asset_storage.ensure_local_file(asset)
    image_role = classify_image_role(asset)
    width, height = _read_image_size(str(image_path))
    ocr = ocr_image_file(image_id=asset.id, image_uri=str(image_path))
    try:
        understanding = get_image_understanding_provider().run(
            asset=asset,
            image_role=image_role,
            ocr_text=ocr.full_text,
            width=width,
            height=height,
        )
    except Exception as exc:
        understanding = _fallback_understanding(asset=asset, image_role=image_role, error=exc)

    tags = list(dict.fromkeys([image_role, *understanding.tags]))
    warnings: list[str] = []
    if width is None or height is None:
        warnings.append("image_size_unavailable")
    if not ocr.full_text:
        warnings.append("ocr_empty")
    if understanding.engine == "unavailable-vlm":
        warnings.append("image_understanding_failed")

    analysis = ImageAssetAnalysis(
        asset_id=asset.id,
        image_uri=asset.uri,
        image_role=image_role,
        width=width,
        height=height,
        ocr=ocr,
        understanding=understanding,
        semantic_summary=understanding.summary,
        tags=tags,
        warnings=warnings,
    )

    payload = analysis.model_dump(mode="json")
    audit_store.record(
        project_id=project_id,
        record_type="agent_output",
        stage="analyze_image_asset",
        payload=payload,
    )

    if ocr.full_text.strip():
        get_memory_store().upsert(
            MemoryUpsertRequest(
                text=ocr.full_text,
                memory_type="product_doc",
                project_id=project_id,
                category=category,
                workflow_type=workflow_type,  # type: ignore[arg-type]
                asset_id=asset.id,
                source_type="image_ocr",
                metadata={"image_role": image_role, "filename": asset.filename},
            )
        )

    understanding_text = _understanding_memory_text(understanding)
    if understanding_text.strip():
        get_memory_store().upsert(
            MemoryUpsertRequest(
                text=understanding_text,
                memory_type=_memory_type_for_image_role(image_role),  # type: ignore[arg-type]
                project_id=project_id,
                category=category,
                workflow_type=workflow_type,  # type: ignore[arg-type]
                asset_id=asset.id,
                source_type="image_understanding",
                metadata={
                    "image_role": image_role,
                    "filename": asset.filename,
                    "engine": understanding.engine,
                },
            )
        )

    return analysis
