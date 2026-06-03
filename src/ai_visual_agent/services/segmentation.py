from abc import ABC, abstractmethod
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any

from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import AssetRef, SegmentationQuality, SegmentationResult
from ai_visual_agent.services.audit_store import audit_store
from ai_visual_agent.services.storage import asset_storage


class SegmentationProvider(ABC):
    engine_name: str

    @abstractmethod
    def run(self, project_id: str, asset: AssetRef, mode: str = "auto") -> SegmentationResult:
        raise NotImplementedError


class MockSegmentationProvider(SegmentationProvider):
    engine_name = "mock-sam2"

    def run(self, project_id: str, asset: AssetRef, mode: str = "auto") -> SegmentationResult:
        image, width, height = _load_rgba_image(asset.uri)
        mask_bytes, transparent_bytes, foreground_ratio = _make_full_subject_outputs(image)
        stem = Path(asset.filename).stem or asset.id

        mask_asset = asset_storage.save_bytes(
            project_id=project_id,
            kind="mask_image",
            filename=f"{stem}_mask.png",
            content=mask_bytes,
            mime_type="image/png",
            metadata={"source_asset_id": asset.id, "segmentation_engine": self.engine_name},
        )
        transparent_asset = asset_storage.save_bytes(
            project_id=project_id,
            kind="transparent_product_image",
            filename=f"{stem}_transparent.png",
            content=transparent_bytes,
            mime_type="image/png",
            metadata={"source_asset_id": asset.id, "segmentation_engine": self.engine_name},
        )

        return SegmentationResult(
            image_id=asset.id,
            image_uri=asset.uri,
            engine=self.engine_name,
            mode=mode,
            mask_asset=mask_asset,
            transparent_asset=transparent_asset,
            quality=SegmentationQuality(
                edge_residue="unknown",
                needs_manual_trim=True,
                foreground_ratio=foreground_ratio,
            ),
        )


class SAM2SegmentationProvider(SegmentationProvider):
    engine_name = "sam2"

    def run(self, project_id: str, asset: AssetRef, mode: str = "auto") -> SegmentationResult:
        settings = get_settings()
        if not settings.sam2_checkpoint or not settings.sam2_model_cfg:
            raise RuntimeError("SAM2_CHECKPOINT and SAM2_MODEL_CFG are required for SEGMENTATION_BACKEND=sam2.")

        try:
            import sam2  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError(
                "SEGMENTATION_BACKEND=sam2 requires SAM 2 dependencies. Install SAM 2 on "
                "segmentation worker machines and configure SAM2_CHECKPOINT / SAM2_MODEL_CFG."
            ) from exc

        raise NotImplementedError(
            "SAM 2 provider wiring is pending. The output contract is ready; implement "
            "SAM2ImagePredictor or automatic mask generation here."
        )


def segment_image_asset(project_id: str, asset: AssetRef, mode: str = "auto") -> SegmentationResult:
    result = get_segmentation_provider().run(project_id=project_id, asset=asset, mode=mode)
    audit_store.record(
        project_id=project_id,
        record_type="agent_output",
        stage="segment_image_asset",
        payload=result.model_dump(mode="json"),
    )
    return result


@lru_cache
def get_segmentation_provider() -> SegmentationProvider:
    backend = get_settings().segmentation_backend.lower()
    if backend in {"sam2", "sam"}:
        return SAM2SegmentationProvider()
    return MockSegmentationProvider()


def _load_rgba_image(uri: str):
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - optional dependency guard
        raise RuntimeError("Pillow is required for segmentation output generation.") from exc

    image = Image.open(uri).convert("RGBA")
    return image, image.width, image.height


def _make_full_subject_outputs(image: Any) -> tuple[bytes, bytes, float]:
    from PIL import Image

    alpha = image.getchannel("A")
    width, height = image.size
    if alpha.getbbox():
        mask = alpha.point(lambda value: 255 if value > 0 else 0)
    else:
        mask = Image.new("L", (width, height), 255)

    transparent = image.copy()
    transparent.putalpha(mask)
    histogram = mask.histogram()
    foreground_pixels = sum(histogram[1:])
    foreground_ratio = foreground_pixels / float(width * height or 1)

    mask_buffer = BytesIO()
    transparent_buffer = BytesIO()
    mask.save(mask_buffer, format="PNG")
    transparent.save(transparent_buffer, format="PNG")
    return mask_buffer.getvalue(), transparent_buffer.getvalue(), foreground_ratio
