from typing import Any

from langchain_core.tools import tool

from ai_visual_agent.domain import AssetRef
from ai_visual_agent.services.design_generation import (
    DesignGenerationJob,
    get_image_generation_provider,
    get_layout_composer,
)


@tool
def generate_design_image(prompt: dict[str, Any], reference_asset_ids: list[str]) -> dict[str, Any]:
    """Generate or edit visual base images using GPT Image 2."""

    job = DesignGenerationJob(
        project_id=str(prompt.get("project_id") or "tool-preview"),
        workflow_type=str(prompt.get("workflow_type") or "packaging"),
        name=str(prompt.get("name") or "tool_preview"),
        prompt=str(prompt.get("prompt") or prompt),
        layout_spec=prompt.get("layout_spec") if isinstance(prompt.get("layout_spec"), dict) else {},
        reference_asset_ids=reference_asset_ids,
        revision_round=int(prompt.get("revision_round") or 0),
    )
    provider = get_image_generation_provider()
    asset = provider.generate_base(job)
    return {
        "engine": provider.engine_name,
        "asset_id": asset.id,
        "uri": asset.uri,
        "revised_prompt": prompt,
        "reference_asset_ids": reference_asset_ids,
    }


@tool
def compose_layout(layout_spec: dict[str, Any], asset_ids: list[str]) -> dict[str, Any]:
    """Programmatically compose logo, copy, labels, compliance text, and generated visuals."""

    job = DesignGenerationJob(
        project_id=str(layout_spec.get("project_id") or "tool-preview"),
        workflow_type=str(layout_spec.get("workflow_type") or "packaging"),
        name=str(layout_spec.get("name") or layout_spec.get("surface") or "tool_preview"),
        prompt=str(layout_spec.get("prompt") or layout_spec),
        layout_spec=layout_spec,
        reference_asset_ids=asset_ids,
        revision_round=int(layout_spec.get("revision_round") or 0),
    )
    base_asset = AssetRef(
        id=asset_ids[0] if asset_ids else "external-base",
        kind="other",
        filename="external-base.png",
        uri=str(layout_spec.get("base_asset_uri") or ""),
        mime_type="image/png",
    )
    composer = get_layout_composer()
    asset = composer.compose(job, base_asset)
    return {
        "engine": composer.engine_name,
        "asset_id": asset.id,
        "uri": asset.uri,
        "layout_spec": layout_spec,
        "asset_ids": asset_ids,
    }
