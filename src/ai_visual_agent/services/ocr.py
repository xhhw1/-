from functools import lru_cache
from typing import Any, Protocol

from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import OCRBlock, OCRResult


class OCRProvider(Protocol):
    engine_name: str

    def run(self, image_id: str, image_uri: str, language: str | None = None) -> OCRResult: ...


class MockOCRProvider:
    engine_name = "mock-paddleocr"

    def __init__(self, mock_text: str = "") -> None:
        self.mock_text = mock_text

    def run(self, image_id: str, image_uri: str, language: str | None = None) -> OCRResult:
        blocks: list[OCRBlock] = []
        if self.mock_text:
            blocks.append(
                OCRBlock(
                    text=self.mock_text,
                    confidence=0.99,
                    bbox=[0, 0, 100, 0, 100, 24, 0, 24],
                )
            )

        return OCRResult(
            image_id=image_id,
            image_uri=image_uri,
            engine=self.engine_name,
            language=language or get_settings().ocr_language,
            blocks=blocks,
            full_text="\n".join(block.text for block in blocks),
        )


class PaddleOCRProvider:
    engine_name = "paddleocr"

    def __init__(self) -> None:
        self._instances: dict[str, Any] = {}

    def run(self, image_id: str, image_uri: str, language: str | None = None) -> OCRResult:
        language = language or get_settings().ocr_language
        paddle_ocr = self._get_instance(language)
        raw_result = self._predict(paddle_ocr, image_uri)
        blocks = self._parse_result(raw_result)
        return OCRResult(
            image_id=image_id,
            image_uri=image_uri,
            engine=self.engine_name,
            language=language,
            blocks=blocks,
            full_text="\n".join(block.text for block in blocks if block.text),
        )

    def _get_instance(self, language: str) -> Any:
        if language in self._instances:
            return self._instances[language]

        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError(
                "OCR_BACKEND=paddle requires PaddleOCR. Install the vision extra or "
                "`paddleocr` + `paddlepaddle` on OCR worker machines."
            ) from exc

        try:
            instance = PaddleOCR(lang=language)
        except TypeError:
            # PaddleOCR 2.x commonly accepted angle classification arguments.
            instance = PaddleOCR(use_angle_cls=True, lang=language)

        self._instances[language] = instance
        return instance

    @staticmethod
    def _predict(paddle_ocr: Any, image_uri: str) -> Any:
        if hasattr(paddle_ocr, "ocr"):
            try:
                return paddle_ocr.ocr(image_uri, cls=True)
            except TypeError:
                return paddle_ocr.ocr(image_uri)
        if hasattr(paddle_ocr, "predict"):
            return paddle_ocr.predict(input=image_uri)
        raise RuntimeError("Unsupported PaddleOCR instance: missing `ocr` or `predict` method.")

    @classmethod
    def _parse_result(cls, raw_result: Any) -> list[OCRBlock]:
        blocks: list[OCRBlock] = []
        for item in cls._walk_result_items(raw_result):
            block = cls._parse_item(item)
            if block and block.text:
                blocks.append(block)
        return blocks

    @classmethod
    def _walk_result_items(cls, raw_result: Any):
        if raw_result is None:
            return
        if isinstance(raw_result, dict):
            yield raw_result
            return
        if hasattr(raw_result, "json"):
            try:
                yield raw_result.json
                return
            except Exception:
                pass
        if isinstance(raw_result, (list, tuple)):
            if cls._looks_like_ocr_item(raw_result):
                yield raw_result
                return
            for child in raw_result:
                yield from cls._walk_result_items(child)

    @staticmethod
    def _looks_like_ocr_item(item: Any) -> bool:
        return (
            isinstance(item, (list, tuple))
            and len(item) >= 2
            and isinstance(item[1], (list, tuple))
            and len(item[1]) >= 2
            and isinstance(item[1][0], str)
        )

    @classmethod
    def _parse_item(cls, item: Any) -> OCRBlock | None:
        if isinstance(item, dict):
            return cls._parse_dict_item(item)
        if cls._looks_like_ocr_item(item):
            bbox = item[0] if item else []
            text_info = item[1]
            return OCRBlock(
                text=str(text_info[0]),
                confidence=float(text_info[1] or 0.0),
                bbox=cls._flatten_bbox(bbox),
            )
        return None

    @staticmethod
    def _parse_dict_item(item: dict[str, Any]) -> OCRBlock | None:
        text = item.get("text") or item.get("rec_text") or item.get("transcription")
        if isinstance(text, list):
            text = "\n".join(str(value) for value in text)
        if not text:
            return None

        confidence = (
            item.get("confidence")
            or item.get("score")
            or item.get("rec_score")
            or item.get("confidence_score")
            or 0.0
        )
        bbox = item.get("bbox") or item.get("points") or item.get("dt_polys") or []
        return OCRBlock(text=str(text), confidence=float(confidence or 0.0), bbox=_safe_flatten_bbox(bbox))

    @staticmethod
    def _flatten_bbox(bbox: Any) -> list[float]:
        return _safe_flatten_bbox(bbox)


def _safe_flatten_bbox(value: Any) -> list[float]:
    flattened: list[float] = []
    if value is None:
        return flattened
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, (list, tuple)):
        if not value:
            return flattened
        for item in value:
            flattened.extend(_safe_flatten_bbox(item))
    return flattened


@lru_cache
def get_ocr_provider() -> OCRProvider:
    settings = get_settings()
    backend = settings.ocr_backend.lower()
    if backend in {"paddle", "paddleocr"}:
        return PaddleOCRProvider()
    return MockOCRProvider(mock_text=settings.mock_ocr_text)
