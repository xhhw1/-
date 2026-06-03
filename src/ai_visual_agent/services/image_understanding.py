from __future__ import annotations

import json
import mimetypes
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import AssetRef, ImageUnderstandingResult


class ImageUnderstandingProvider(Protocol):
    engine_name: str

    def run(
        self,
        *,
        asset: AssetRef,
        image_role: str,
        ocr_text: str = "",
        width: int | None = None,
        height: int | None = None,
    ) -> ImageUnderstandingResult: ...


class MockImageUnderstandingProvider:
    engine_name = "mock-vlm"

    def run(
        self,
        *,
        asset: AssetRef,
        image_role: str,
        ocr_text: str = "",
        width: int | None = None,
        height: int | None = None,
    ) -> ImageUnderstandingResult:
        dimensions = f"{width}x{height}" if width and height else "unknown size"
        tags = [image_role, "visual_understanding_mock"]
        ocr_hint = f"OCR text is available: {ocr_text[:80]}" if ocr_text else "No OCR text detected."

        product_appearance: list[str] = []
        visible_accessories: list[str] = []
        play_clues: list[str] = []
        competitor_visual_hooks: list[str] = []
        packaging_hierarchy: list[str] = []
        detail_page_sections: list[str] = []
        risks = ["Replace mock output with a real multimodal backend before production sign-off."]

        if image_role == "product_image":
            product_appearance = [
                "Primary product subject is treated as the source-of-truth shape reference.",
                "Use this image to constrain downstream product consistency checks.",
            ]
            play_clues = ["Infer exact play mechanics from product PPT, video, or manual review."]
        elif image_role in {"competitor_image", "competitor_packaging", "competitor_detail_page"}:
            competitor_visual_hooks = [
                "Capture headline hierarchy, hero angle, color contrast, and shelf-impact cues.",
                "Use hooks as competitive references, not as direct design copies.",
            ]
            tags.append("competitor_reference")
        elif image_role in {"packaging_image", "vi_reference_image", "logo"}:
            packaging_hierarchy = [
                "Extract logo placement, brand color usage, type hierarchy, and forbidden layout cues.",
            ]
            tags.append("vi_reference")
        elif image_role == "detail_page_image":
            detail_page_sections = [
                "Break the image into traffic hook, feature proof, scenario, accessory, and conversion cues.",
            ]

        return ImageUnderstandingResult(
            image_id=asset.id,
            image_uri=asset.uri,
            engine=self.engine_name,
            image_role=image_role,
            summary=f"{image_role} analyzed by mock VLM ({dimensions}). {ocr_hint}",
            product_appearance=product_appearance,
            visible_accessories=visible_accessories,
            play_clues=play_clues,
            competitor_visual_hooks=competitor_visual_hooks,
            packaging_hierarchy=packaging_hierarchy,
            detail_page_sections=detail_page_sections,
            risks=risks,
            tags=tags,
        )


class GeminiImageUnderstandingProvider:
    engine_name = "gemini-vlm"

    def run(
        self,
        *,
        asset: AssetRef,
        image_role: str,
        ocr_text: str = "",
        width: int | None = None,
        height: int | None = None,
    ) -> ImageUnderstandingResult:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is required for MULTIMODAL_BACKEND=gemini.")

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError(
                "MULTIMODAL_BACKEND=gemini requires google-genai. Install the vision extra."
            ) from exc

        image_path = Path(asset.uri)
        mime_type = asset.mime_type or mimetypes.guess_type(asset.uri)[0] or "image/png"
        prompt = _build_prompt(image_role=image_role, ocr_text=ocr_text, width=width, height=height)
        client = genai.Client(api_key=settings.gemini_api_key)
        response = client.models.generate_content(
            model=settings.multimodal_model,
            contents=[
                types.Part.from_text(text=prompt),
                types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime_type),
            ],
        )
        text = getattr(response, "text", "") or ""
        return _parse_model_response(
            text=text,
            asset=asset,
            image_role=image_role,
            engine=self.engine_name,
        )


class OpenAIImageUnderstandingProvider:
    engine_name = "openai-vlm"

    def run(
        self,
        *,
        asset: AssetRef,
        image_role: str,
        ocr_text: str = "",
        width: int | None = None,
        height: int | None = None,
    ) -> ImageUnderstandingResult:
        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for MULTIMODAL_BACKEND=openai.")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("MULTIMODAL_BACKEND=openai requires the openai package.") from exc

        prompt = _build_prompt(image_role=image_role, ocr_text=ocr_text, width=width, height=height)
        client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
        response = client.chat.completions.create(
            model=settings.multimodal_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": _local_image_data_url(asset)}},
                    ],
                }
            ],
        )
        text = response.choices[0].message.content or ""
        return _parse_model_response(
            text=text,
            asset=asset,
            image_role=image_role,
            engine=self.engine_name,
        )


class OpenAICompatibleImageUnderstandingProvider:
    engine_name = "openai-compatible-vlm"

    def run(
        self,
        *,
        asset: AssetRef,
        image_role: str,
        ocr_text: str = "",
        width: int | None = None,
        height: int | None = None,
    ) -> ImageUnderstandingResult:
        settings = get_settings()
        api_key = settings.multimodal_api_key or settings.gemini_api_key or settings.openai_api_key
        base_url = settings.multimodal_base_url or settings.openai_base_url
        if not api_key:
            raise RuntimeError(
                "MULTIMODAL_BACKEND=openai_compatible requires MULTIMODAL_API_KEY, "
                "GEMINI_API_KEY, or OPENAI_API_KEY."
            )

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("MULTIMODAL_BACKEND=openai_compatible requires the openai package.") from exc

        prompt = _build_prompt(image_role=image_role, ocr_text=ocr_text, width=width, height=height)
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=settings.multimodal_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": _local_image_data_url(asset)}},
                    ],
                }
            ],
        )
        text = response.choices[0].message.content or ""
        return _parse_model_response(
            text=text,
            asset=asset,
            image_role=image_role,
            engine=self.engine_name,
        )


def _build_prompt(
    *,
    image_role: str,
    ocr_text: str,
    width: int | None,
    height: int | None,
) -> str:
    return (
        "You are an ecommerce visual analysis agent. Return strict JSON matching these keys: "
        "summary, product_appearance, visible_accessories, play_clues, competitor_visual_hooks, "
        "packaging_hierarchy, detail_page_sections, risks, tags. "
        f"image_role={image_role}; width={width}; height={height}; OCR={ocr_text!r}. "
        "Do not invent product facts that are not visible. Mark uncertain points as risks."
    )


def _parse_model_response(
    *,
    text: str,
    asset: AssetRef,
    image_role: str,
    engine: str,
) -> ImageUnderstandingResult:
    payload = _load_json_payload(text)
    return ImageUnderstandingResult(
        image_id=asset.id,
        image_uri=asset.uri,
        engine=engine,
        image_role=image_role,
        summary=str(payload.get("summary") or text[:500]),
        product_appearance=_string_list(payload.get("product_appearance")),
        visible_accessories=_string_list(payload.get("visible_accessories")),
        play_clues=_string_list(payload.get("play_clues")),
        competitor_visual_hooks=_string_list(payload.get("competitor_visual_hooks")),
        packaging_hierarchy=_string_list(payload.get("packaging_hierarchy")),
        detail_page_sections=_string_list(payload.get("detail_page_sections")),
        risks=_string_list(payload.get("risks")),
        tags=_string_list(payload.get("tags")),
    )


def _load_json_payload(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        return {"summary": text, "risks": ["Model response was not valid JSON."]}
    return value if isinstance(value, dict) else {"summary": text, "risks": ["JSON root was not an object."]}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _local_image_data_url(asset: AssetRef) -> str:
    import base64

    path = Path(asset.uri)
    mime_type = asset.mime_type or mimetypes.guess_type(asset.uri)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{data}"


@lru_cache
def get_image_understanding_provider() -> ImageUnderstandingProvider:
    backend = get_settings().multimodal_backend.lower()
    if backend == "gemini":
        return GeminiImageUnderstandingProvider()
    if backend == "openai":
        return OpenAIImageUnderstandingProvider()
    if backend in {"openai_compatible", "openai-compatible", "shiyun"}:
        return OpenAICompatibleImageUnderstandingProvider()
    return MockImageUnderstandingProvider()
