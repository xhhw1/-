from typing import Any

from langchain_core.tools import tool

from ai_visual_agent.domain import AssetRef, ImageUnderstandingResult, OCRResult
from ai_visual_agent.services.image_understanding import get_image_understanding_provider
from ai_visual_agent.services.ocr import get_ocr_provider
from ai_visual_agent.services.segmentation import get_segmentation_provider


def ocr_image_file(image_id: str, image_uri: str, language: str = "ch") -> OCRResult:
    """Run OCR over an image and return text blocks with coordinates and confidence."""

    return get_ocr_provider().run(image_id=image_id, image_uri=image_uri, language=language)


@tool
def ocr_image(image_id: str, image_uri: str, language: str = "ch") -> dict[str, Any]:
    """Run OCR over an image and return text blocks with coordinates and confidence."""

    return ocr_image_file(image_id=image_id, image_uri=image_uri, language=language).model_dump()


def understand_image_file(
    image_id: str,
    image_uri: str,
    image_role: str = "product_image",
    ocr_text: str = "",
) -> ImageUnderstandingResult:
    """Run the configured multimodal image understanding provider."""

    asset = AssetRef(id=image_id, kind="product_image", filename=f"{image_id}.png", uri=image_uri)
    return get_image_understanding_provider().run(
        asset=asset,
        image_role=image_role,
        ocr_text=ocr_text,
    )


@tool
def understand_image(
    image_id: str,
    image_uri: str,
    image_role: str = "product_image",
    ocr_text: str = "",
) -> dict[str, Any]:
    """Understand product, competitor, packaging, VI, or detail-page image semantics."""

    return understand_image_file(
        image_id=image_id,
        image_uri=image_uri,
        image_role=image_role,
        ocr_text=ocr_text,
    ).model_dump()


@tool
def segment_product(
    image_id: str,
    image_uri: str,
    project_id: str = "tool-run",
    mode: str = "auto",
) -> dict[str, Any]:
    """Segment product subject and produce mask plus transparent PNG asset references."""

    asset = AssetRef(id=image_id, kind="product_image", filename=f"{image_id}.png", uri=image_uri)
    return get_segmentation_provider().run(project_id=project_id, asset=asset, mode=mode).model_dump()


@tool
def analyze_product_image(image_id: str, image_uri: str) -> dict[str, Any]:
    """Use a multimodal model to understand product appearance, visible features, and risks."""

    return understand_image_file(
        image_id=image_id,
        image_uri=image_uri,
        image_role="product_image",
    ).model_dump()
