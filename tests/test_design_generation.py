import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_visual_agent.domain import AssetRef, ProjectBrief, ProjectRecord
from ai_visual_agent.graph.nodes import generate_design_node
from ai_visual_agent.services import design_generation
from ai_visual_agent.services.audit_store import audit_store
from ai_visual_agent.services.design_generation import (
    OpenAIImageGenerationProvider,
    generate_design_outputs,
)
from ai_visual_agent.services.storage import asset_storage


def _patch_asset_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(asset_storage, "root", tmp_path / "assets")


def test_generate_packaging_design_outputs_create_png_assets(monkeypatch, tmp_path) -> None:
    _patch_asset_root(monkeypatch, tmp_path)

    output = generate_design_outputs(
        project_id="design-project",
        workflow_type="packaging",
        strategy={
            "product_name": "测试玩具",
            "front_layout": "front hero product",
            "left_layout": "left play proof",
            "right_layout": "right accessories",
            "back_layout": "back compliance",
            "required_copy": ["核心卖点"],
        },
        vi_profile={"brand_colors": ["red", "blue"]},
        revision_round=0,
        reference_asset_ids=["product-asset"],
    )

    assert output.revision_round == 0
    assert [item.name for item in output.items] == ["front", "left", "right", "back"]
    for item in output.items:
        path = Path(item.uri)
        assert path.exists()
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert item.layout_spec["base_asset_id"]
        assert "mock-gpt-image-2" in item.layout_spec["image_engine"]
        assert item.layout_spec["layout_engine"] == "disabled"
        assert "composed_asset_id" not in item.layout_spec

    records = audit_store.list_records("design-project", record_type="agent_output")
    assert any(record.stage == "generate_design_assets" for record in records)


def test_generate_packaging_main_image_uses_confirmed_prompt_only(monkeypatch, tmp_path) -> None:
    _patch_asset_root(monkeypatch, tmp_path)

    output = generate_design_outputs(
        project_id="main-image-project",
        workflow_type="packaging",
        strategy={
            "product_name": "测试玩具",
            "front_layout": "front fallback",
            "left_layout": "left should not run",
            "right_layout": "right should not run",
            "back_layout": "back should not run",
            "required_copy": ["核心卖点"],
        },
        vi_profile={"brand_colors": ["purple"]},
        revision_round=0,
        reference_asset_ids=["product-asset", "logo-asset"],
        main_image_prompt_draft={
            "main_image_prompt": "确认后的主图提示词：严格参考产品图，预留中文标题区。",
            "negative_prompt": "不要英文伪字。",
            "reference_usage": "产品图锁定外观，LOGO 只做位置参考。",
            "layout_notes": "正面主图。",
            "text_overlay_plan": ["核心卖点"],
        },
    )

    assert [item.name for item in output.items] == ["front"]
    item = output.items[0]
    assert item.prompt.startswith("确认后的主图提示词")
    assert item.layout_spec["surface"] == "front"
    assert item.layout_spec["prebuilt_image_prompt"].startswith("确认后的主图提示词")
    assert "不要英文伪字" in item.layout_spec["full_image_prompt"]
    assert "只允许一处卖点文字表达区" in item.layout_spec["full_image_prompt"]
    assert "正面主图不要生成认证标识区" in item.layout_spec["full_image_prompt"]
    assert item.layout_spec["reference_asset_ids"] == ["product-asset", "logo-asset"]
    assert item.layout_spec["actual_reference_asset_ids"] == ["product-asset", "logo-asset"]


def test_generate_design_node_uses_provider_outputs(monkeypatch, tmp_path) -> None:
    _patch_asset_root(monkeypatch, tmp_path)

    result = generate_design_node(
        {
            "project_id": "node-project",
            "workflow_type": "detail_page",
            "revision_round": 1,
            "assets": [{"id": "product-1", "kind": "product_image"}],
            "vi_profile": {"brand_colors": ["green"]},
            "detail_page_strategy": {
                "screens": [
                    {
                        "screen_index": 1,
                        "goal": "first screen hook",
                        "visual": "large product",
                        "copy_text": "main selling point",
                    },
                    {
                        "screen_index": 2,
                        "goal": "feature proof",
                        "visual": "play steps",
                        "copy_text": "clear play proof",
                    },
                ]
            },
        }
    )

    assert result["status"] == "design_generated"
    items = result["generated_outputs"]["items"]
    assert [item["name"] for item in items] == ["screen_1", "screen_2"]
    assert all(Path(item["uri"]).exists() for item in items)
    assert items[0]["layout_spec"]["revision_round"] == 1
    assert items[0]["layout_spec"]["base_asset_id"]


def test_generate_design_outputs_falls_back_when_real_provider_fails(monkeypatch, tmp_path) -> None:
    _patch_asset_root(monkeypatch, tmp_path)

    class BrokenProvider:
        engine_name = "broken-real-image"

        def generate_base(self, job):
            raise RuntimeError("image gateway unavailable")

    monkeypatch.setattr(design_generation, "get_image_generation_provider", lambda: BrokenProvider())

    output = generate_design_outputs(
        project_id="fallback-project",
        workflow_type="packaging",
        strategy={
            "product_name": "测试玩具",
            "front_layout": "front hero product",
            "left_layout": "left play proof",
            "right_layout": "right accessories",
            "back_layout": "back compliance",
        },
        vi_profile={},
        revision_round=0,
        reference_asset_ids=[],
    )

    assert output.items[0].layout_spec["image_generation_fallback_used"] is True
    assert "image gateway unavailable" in output.items[0].layout_spec["image_generation_error"]
    assert output.items[0].layout_spec["image_engine"] == "broken-real-image->mock-gpt-image-2"
    assert Path(output.items[0].uri).exists()


def test_generate_design_outputs_returns_partial_on_later_failure(monkeypatch, tmp_path) -> None:
    _patch_asset_root(monkeypatch, tmp_path)
    generated = []
    errors = []

    class OneThenBrokenProvider:
        engine_name = "quota-limited-image"

        def generate_base(self, job):
            if job.name != "front":
                raise RuntimeError("token quota is not enough")
            return asset_storage.save_bytes(
                project_id=job.project_id,
                kind="other",
                filename="front_base.png",
                content=b"\x89PNG\r\n\x1a\nfront",
                mime_type="image/png",
                metadata={"engine": self.engine_name},
            )

    monkeypatch.setattr(design_generation, "get_image_generation_provider", lambda: OneThenBrokenProvider())

    output = generate_design_outputs(
        project_id="partial-project",
        workflow_type="packaging",
        strategy={
            "product_name": "测试玩具",
            "front_layout": "front hero product",
            "left_layout": "left play proof",
            "right_layout": "right accessories",
            "back_layout": "back compliance",
        },
        vi_profile={},
        revision_round=0,
        reference_asset_ids=["product-asset"],
        allow_mock_fallback=False,
        return_partial_on_error=True,
        on_item_generated=lambda item, items, total: generated.append((item.name, len(items), total)),
        on_generation_error=lambda job, error, items, total: errors.append((job.name, error, len(items), total)),
    )

    assert [item.name for item in output.items] == ["front"]
    assert output.items[0].asset_id == output.items[0].layout_spec["base_asset_id"]
    assert generated == [("front", 1, 4)]
    assert errors[0][0] == "left"
    assert "token quota is not enough" in errors[0][1]["error"]
    assert errors[0][2:] == (1, 4)


def test_openai_image_provider_saves_b64_response(monkeypatch, tmp_path) -> None:
    _patch_asset_root(monkeypatch, tmp_path)
    png_bytes = b"\x89PNG\r\n\x1a\nfake"

    class FakeImages:
        def __init__(self) -> None:
            self.generate_kwargs = None

        def generate(self, **kwargs):
            self.generate_kwargs = kwargs
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(png_bytes).decode("ascii"))]
            )

    class FakeClient:
        def __init__(self) -> None:
            self.images = FakeImages()

    fake_client = FakeClient()
    monkeypatch.setattr(design_generation, "_create_openai_image_client", lambda settings: fake_client)
    monkeypatch.setattr(design_generation, "_reference_image_paths", lambda job: [])

    class Settings:
        openai_api_key = "configured"
        openai_base_url = "https://shiyunapi.com/v1"
        openai_image_model = "gpt-image-2"
        image_generation_quality = "low"

    monkeypatch.setattr(design_generation, "get_settings", lambda: Settings())

    provider = OpenAIImageGenerationProvider()
    asset = provider.generate_base(
        design_generation.DesignGenerationJob(
            project_id="openai-image-project",
            workflow_type="packaging",
            name="front",
            prompt="front hero product",
            layout_spec={"surface": "front", "tone": "bright"},
        )
    )

    assert Path(asset.uri).read_bytes() == png_bytes
    assert asset.metadata["engine"] == "openai-image"
    assert fake_client.images.generate_kwargs["model"] == "gpt-image-2"
    assert fake_client.images.generate_kwargs["size"] == "1024x1024"


def test_openai_image_provider_uses_reference_edit(monkeypatch, tmp_path) -> None:
    _patch_asset_root(monkeypatch, tmp_path)
    reference_path = tmp_path / "reference.png"
    reference_path.write_bytes(b"\x89PNG\r\n\x1a\nref")
    png_bytes = b"\x89PNG\r\n\x1a\nedited"

    class FakeImages:
        def __init__(self) -> None:
            self.edit_kwargs = None
            self.generate_called = False

        def edit(self, **kwargs):
            self.edit_kwargs = kwargs
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(png_bytes).decode("ascii"))])

        def generate(self, **kwargs):
            self.generate_called = True
            raise AssertionError("generate should not be used when reference image exists")

    class FakeClient:
        def __init__(self) -> None:
            self.images = FakeImages()

    fake_client = FakeClient()
    monkeypatch.setattr(design_generation, "_create_openai_image_client", lambda settings: fake_client)
    monkeypatch.setattr(design_generation, "_reference_image_paths", lambda job: [reference_path])

    class Settings:
        openai_api_key = "configured"
        openai_base_url = "https://api.openai.com/v1"
        openai_image_model = "gpt-image-2"
        image_generation_quality = "low"

    monkeypatch.setattr(design_generation, "get_settings", lambda: Settings())

    provider = OpenAIImageGenerationProvider()
    asset = provider.generate_base(
        design_generation.DesignGenerationJob(
            project_id="edit-project",
            workflow_type="packaging",
            name="front",
            prompt="正面海报",
            layout_spec={"surface": "front", "tone": "明亮"},
            reference_asset_ids=["product-image"],
        )
    )

    assert Path(asset.uri).read_bytes() == png_bytes
    assert fake_client.images.edit_kwargs["prompt"].startswith("你是电商视觉设计出图引擎")
    assert fake_client.images.edit_kwargs["image"] is not None
    assert fake_client.images.generate_called is False


def test_openai_image_provider_does_not_silently_drop_reference(monkeypatch, tmp_path) -> None:
    reference_path = tmp_path / "reference.png"
    reference_path.write_bytes(b"\x89PNG\r\n\x1a\nref")

    class FakeImages:
        def __init__(self) -> None:
            self.generate_called = False

        def edit(self, **kwargs):
            raise RuntimeError("edit unsupported")

        def generate(self, **kwargs):
            self.generate_called = True
            return SimpleNamespace(data=[])

    class FakeClient:
        def __init__(self) -> None:
            self.images = FakeImages()

    fake_client = FakeClient()
    monkeypatch.setattr(design_generation, "_create_openai_image_client", lambda settings: fake_client)
    monkeypatch.setattr(design_generation, "_reference_image_paths", lambda job: [reference_path])

    class Settings:
        openai_api_key = "configured"
        openai_base_url = "https://api.openai.com/v1"
        openai_image_model = "gpt-image-2"
        image_generation_quality = "low"

    monkeypatch.setattr(design_generation, "get_settings", lambda: Settings())

    provider = OpenAIImageGenerationProvider()
    with pytest.raises(RuntimeError, match="product reference"):
        provider.generate_base(
            design_generation.DesignGenerationJob(
                project_id="edit-fail-project",
                workflow_type="packaging",
                name="front",
                prompt="正面海报",
                layout_spec={"surface": "front"},
                reference_asset_ids=["product-image"],
            )
        )
    assert fake_client.images.generate_called is False


def test_shiyun_all_provider_sends_multi_reference_image_array(monkeypatch, tmp_path) -> None:
    ref_a = tmp_path / "product.png"
    ref_b = tmp_path / "logo.png"
    ref_a.write_bytes(b"\x89PNG\r\n\x1a\nproduct")
    ref_b.write_bytes(b"\x89PNG\r\n\x1a\nlogo")
    captured = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"\x89PNG\r\n\x1a\nok").decode("ascii")}]}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)

    class Settings:
        openai_api_key = "configured"
        openai_base_url = "https://shiyunapi.com/v1"

    response = design_generation._generate_shiyun_with_reference_image(
        settings=Settings(),
        reference_paths=[ref_a, ref_b],
        prompt="多图参考",
        model="gpt-image-2-all",
        size="1024x1024",
        quality="low",
    )

    payload = captured["kwargs"]["json"]
    assert captured["url"] == "https://shiyunapi.com/v1/images/generations"
    assert payload["model"] == "gpt-image-2-all"
    assert isinstance(payload["image"], list)
    assert len(payload["image"]) == 2
    assert payload["image"][0].startswith("data:image/png;base64,")
    assert response["data"][0]["b64_json"]


def test_shiyun_provider_includes_error_body_on_http_failure(monkeypatch, tmp_path) -> None:
    reference_path = tmp_path / "product.png"
    reference_path.write_bytes(b"\x89PNG\r\n\x1a\nproduct")

    class FakeResponse:
        status_code = 403
        text = '{"error":{"message":"no access to model"}}'

        def raise_for_status(self) -> None:
            import httpx

            request = httpx.Request("POST", "https://shiyunapi.com/v1/images/generations")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("forbidden", request=request, response=response)

    def fake_post(url, **kwargs):
        return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)

    class Settings:
        openai_api_key = "configured"
        openai_base_url = "https://shiyunapi.com/v1"
        image_generation_timeout = 420

    with pytest.raises(RuntimeError, match="no access to model"):
        design_generation._generate_shiyun_with_reference_image(
            settings=Settings(),
            reference_paths=[reference_path],
            prompt="多图参考",
            model="gpt-image-2-all",
            size="1024x1024",
            quality="low",
        )


def test_shiyun_provider_retries_transient_failures(monkeypatch, tmp_path) -> None:
    reference_path = tmp_path / "product.png"
    reference_path.write_bytes(b"\x89PNG\r\n\x1a\nproduct")
    calls = []

    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.text = "{}"

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                import httpx

                request = httpx.Request("POST", "https://shiyunapi.com/v1/images/generations")
                response = httpx.Response(self.status_code, request=request, text=self.text)
                raise httpx.HTTPStatusError("temporary", request=request, response=response)

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"\x89PNG\r\n\x1a\nok").decode("ascii")}]}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(500 if len(calls) == 1 else 200)

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(design_generation.time, "sleep", lambda _seconds: None)

    class Settings:
        openai_api_key = "configured"
        openai_base_url = "https://shiyunapi.com/v1"
        image_generation_timeout = 420

    response = design_generation._generate_shiyun_with_reference_image(
        settings=Settings(),
        reference_paths=[reference_path],
        prompt="多图参考",
        model="gpt-image-2-all",
        size="1024x1024",
        quality="low",
    )

    assert len(calls) == 2
    assert response["data"][0]["b64_json"]


def test_shiyun_provider_retries_ssl_eof_connect_errors(monkeypatch, tmp_path) -> None:
    reference_path = tmp_path / "product.png"
    reference_path.write_bytes(b"\x89PNG\r\n\x1a\nproduct")
    calls = []

    class FakeResponse:
        status_code = 200
        text = "{}"

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"\x89PNG\r\n\x1a\nok").decode("ascii")}]}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) < 3:
            import httpx

            request = httpx.Request("POST", "https://shiyunapi.com/v1/images/generations")
            raise httpx.ConnectError(
                "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol",
                request=request,
            )
        return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(design_generation.time, "sleep", lambda _seconds: None)

    class Settings:
        openai_api_key = "configured"
        openai_base_url = "https://shiyunapi.com/v1"
        image_generation_timeout = 420

    response = design_generation._generate_shiyun_with_reference_image(
        settings=Settings(),
        reference_paths=[reference_path],
        prompt="ssl eof retry",
        model="gpt-image-2-all",
        size="1024x1024",
        quality="low",
    )

    assert len(calls) == 3
    assert response["data"][0]["b64_json"]


def test_reference_ids_are_not_silently_dropped() -> None:
    with pytest.raises(RuntimeError, match="Reference asset ids"):
        design_generation._generate_with_reference_if_available(
            settings=SimpleNamespace(openai_base_url="https://api.openai.com/v1"),
            client=SimpleNamespace(images=SimpleNamespace()),
            job=design_generation.DesignGenerationJob(
                project_id="missing-project",
                workflow_type="packaging",
                name="front",
                prompt="正面",
                layout_spec={},
                reference_asset_ids=["missing-product"],
            ),
            prompt="正面",
            model="gpt-image-2",
            size="1024x1024",
            quality="low",
        )


def test_reference_image_paths_follow_job_reference_order(monkeypatch, tmp_path) -> None:
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    first_path.write_bytes(b"\x89PNG\r\n\x1a\nfirst")
    second_path.write_bytes(b"\x89PNG\r\n\x1a\nsecond")
    project = ProjectRecord(
        id="ordered-reference-project",
        workflow_type="packaging",
        brief=ProjectBrief(),
        assets=[
            AssetRef(id="second", kind="logo", filename="second.png", uri=str(second_path), mime_type="image/png"),
            AssetRef(id="first", kind="product_image", filename="first.png", uri=str(first_path), mime_type="image/png"),
        ],
    )
    monkeypatch.setattr(design_generation.project_store, "get", lambda project_id: project)

    paths = design_generation._reference_image_paths(
        design_generation.DesignGenerationJob(
            project_id=project.id,
            workflow_type="packaging",
            name="front",
            prompt="",
            layout_spec={},
            reference_asset_ids=["first", "second"],
        )
    )

    assert paths == [first_path, second_path]

    job = design_generation.DesignGenerationJob(
        project_id=project.id,
        workflow_type="packaging",
        name="front",
        prompt="",
        layout_spec={},
        reference_asset_ids=["first", "second"],
    )
    assert design_generation._actual_reference_asset_ids_for_request(
        settings=SimpleNamespace(openai_base_url="https://api.openai.com/v1"),
        job=job,
        model="gpt-image-2",
    ) == ["first"]
    assert design_generation._actual_reference_asset_ids_for_request(
        settings=SimpleNamespace(openai_base_url="https://shiyunapi.com/v1"),
        job=job,
        model="gpt-image-2-all",
    ) == ["first", "second"]


def test_selective_image_generation_runs_real_front_only(monkeypatch, tmp_path) -> None:
    _patch_asset_root(monkeypatch, tmp_path)
    png_bytes = b"\x89PNG\r\n\x1a\nfront"

    class FakeImages:
        def __init__(self) -> None:
            self.generate_calls = []

        def generate(self, **kwargs):
            self.generate_calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(png_bytes).decode("ascii"))]
            )

    class FakeClient:
        def __init__(self) -> None:
            self.images = FakeImages()

    fake_client = FakeClient()
    monkeypatch.setattr(design_generation, "_create_openai_image_client", lambda settings: fake_client)
    monkeypatch.setattr(design_generation, "_reference_image_paths", lambda job: [])

    class Settings:
        mock_external_tools = True
        image_generation_backend = "openai"
        image_generation_real_names = "front"
        image_generation_quality = "low"
        openai_api_key = "configured"
        openai_base_url = "https://shiyunapi.com/v1"
        openai_image_model = "gpt-image-2"

    monkeypatch.setattr(design_generation, "get_settings", lambda: Settings())
    design_generation.get_image_generation_provider.cache_clear()

    output = generate_design_outputs(
        project_id="selective-project",
        workflow_type="packaging",
        strategy={
            "product_name": "测试玩具",
            "front_layout": "front hero product",
            "left_layout": "left play proof",
            "right_layout": "right accessories",
            "back_layout": "back compliance",
        },
        vi_profile={},
        revision_round=0,
        reference_asset_ids=[],
    )

    engines = {item.name: item.layout_spec["image_engine"] for item in output.items}
    assert engines == {
        "front": "openai-image",
        "left": "mock-gpt-image-2",
        "right": "mock-gpt-image-2",
        "back": "mock-gpt-image-2",
    }
    assert len(fake_client.images.generate_calls) == 1
    assert fake_client.images.generate_calls[0]["quality"] == "low"
    design_generation.get_image_generation_provider.cache_clear()
