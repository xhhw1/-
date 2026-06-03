from typing import Any

from langchain_core.tools import tool

from ai_visual_agent.config import get_settings


@tool
def analyze_competitor_video(video_id: str, video_uri: str) -> dict[str, Any]:
    """Analyze competitor ecommerce or short-video material for hooks, claims, and visual structure."""

    if get_settings().mock_external_tools:
        return {
            "video_id": video_id,
            "video_uri": video_uri,
            "engine": "mock-gemini-video",
            "hooks": [],
            "selling_points": [],
            "scene_sequence": [],
            "timestamps": [],
        }

    # Production implementation: Gemini File API + video understanding prompt.
    raise NotImplementedError("Competitor video analyzer is not wired yet.")
