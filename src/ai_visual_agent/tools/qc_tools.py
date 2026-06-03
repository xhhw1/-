from typing import Any

from langchain_core.tools import tool

from ai_visual_agent.config import get_settings


@tool
def run_visual_qc(output_asset_id: str, reference_asset_ids: list[str]) -> dict[str, Any]:
    """Check product consistency, VI compliance, OCR text, layout, and compliance risks."""

    if get_settings().mock_external_tools:
        return {
            "output_asset_id": output_asset_id,
            "reference_asset_ids": reference_asset_ids,
            "passed": True,
            "score": 0.9,
            "issues": [],
            "summary": "Mock QC passed. Production QC will combine OCR, CV metrics, and VLM review.",
        }

    # Production implementation:
    # - OCR generated image and compare required copy.
    # - Delta E / palette match for brand colors.
    # - Mask/SSIM/feature checks for product consistency.
    # - VLM review for layout and obvious product deformation.
    raise NotImplementedError("Visual QC provider is not wired yet.")
