from ai_visual_agent.domain import AssetRef
from ai_visual_agent.services.image_understanding import (
    MockImageUnderstandingProvider,
    get_image_understanding_provider,
)
from ai_visual_agent.tools.vision_tools import understand_image_file


def test_mock_image_understanding_product_output() -> None:
    asset = AssetRef(
        id="img-1",
        kind="product_image",
        filename="hero.png",
        uri="C:/tmp/hero.png",
        mime_type="image/png",
    )

    result = MockImageUnderstandingProvider().run(
        asset=asset,
        image_role="product_image",
        ocr_text="Safe material",
        width=800,
        height=800,
    )

    assert result.engine == "mock-vlm"
    assert result.product_appearance
    assert "product_image" in result.tags


def test_mock_image_understanding_competitor_output() -> None:
    asset = AssetRef(
        id="img-2",
        kind="competitor_packaging",
        filename="packaging.png",
        uri="C:/tmp/packaging.png",
        mime_type="image/png",
    )

    result = MockImageUnderstandingProvider().run(asset=asset, image_role="competitor_packaging")

    assert result.competitor_visual_hooks
    assert "competitor_reference" in result.tags


def test_langchain_tool_uses_configured_understanding_provider() -> None:
    get_image_understanding_provider.cache_clear()

    result = understand_image_file(
        image_id="img-3",
        image_uri="C:/tmp/hero.png",
        image_role="product_image",
    )

    assert result.engine == "mock-vlm"
