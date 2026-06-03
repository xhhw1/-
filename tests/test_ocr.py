from ai_visual_agent.services.ocr import MockOCRProvider, PaddleOCRProvider


def test_mock_ocr_provider_can_return_configured_text() -> None:
    result = MockOCRProvider(mock_text="LOGO 安全区").run(
        image_id="image-1",
        image_uri="C:/tmp/image.png",
        language="ch",
    )

    assert result.engine == "mock-paddleocr"
    assert result.full_text == "LOGO 安全区"
    assert result.blocks[0].confidence == 0.99


def test_paddleocr_v2_result_parser() -> None:
    raw_result = [
        [
            [
                [[0, 0], [10, 0], [10, 10], [0, 10]],
                ("核心卖点", 0.93),
            ]
        ]
    ]

    blocks = PaddleOCRProvider._parse_result(raw_result)

    assert len(blocks) == 1
    assert blocks[0].text == "核心卖点"
    assert blocks[0].confidence == 0.93
    assert blocks[0].bbox == [0.0, 0.0, 10.0, 0.0, 10.0, 10.0, 0.0, 10.0]
