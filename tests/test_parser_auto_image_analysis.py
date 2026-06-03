from ai_visual_agent.domain import ImageAssetAnalysis, ImageUnderstandingResult, OCRResult
from ai_visual_agent.graph import nodes


def test_parse_inputs_auto_analyzes_image_assets(monkeypatch) -> None:
    def fake_analyze_image_asset(*, project_id, asset, workflow_type=None, category=None):
        assert project_id == "project-1"
        assert workflow_type == "packaging"
        assert category == "toy"
        return ImageAssetAnalysis(
            asset_id=asset.id,
            image_uri=asset.uri,
            image_role="product_image",
            width=512,
            height=512,
            ocr=OCRResult(
                image_id=asset.id,
                image_uri=asset.uri,
                engine="fake-ocr",
                full_text="安全材质",
            ),
            understanding=ImageUnderstandingResult(
                image_id=asset.id,
                image_uri=asset.uri,
                engine="fake-vlm",
                image_role="product_image",
                summary="产品主体是蓝色互动玩具。",
                product_appearance=["蓝色圆形主体"],
                visible_accessories=["遥控器配件"],
                play_clues=["亲子互动玩法"],
            ),
            semantic_summary="产品主体是蓝色互动玩具。",
            tags=["product_image"],
        )

    monkeypatch.setattr(nodes, "analyze_image_asset", fake_analyze_image_asset)

    result = nodes.parse_inputs_node(
        {
            "project_id": "project-1",
            "workflow_type": "packaging",
            "project_brief": {
                "category": "toy",
                "core_product_definition": "interactive toy set",
            },
            "assets": [
                {
                    "id": "hero-image",
                    "kind": "product_image",
                    "filename": "hero.png",
                    "uri": "C:/tmp/hero.png",
                    "mime_type": "image/png",
                    "metadata": {},
                }
            ],
        }
    )

    assert result["assets"][0]["metadata"]["image_analysis"]["understanding"]["engine"] == "fake-vlm"
    assert "蓝色圆形主体" in result["parsed_product"]["visual_features"]
    assert "遥控器配件" in result["parsed_product"]["visual_features"]
