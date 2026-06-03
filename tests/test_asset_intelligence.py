from ai_visual_agent.domain import AssetRef, PackagingStrategy, ProjectBrief, ProjectCreateRequest, ProjectRecord, USPCandidates, USPItem
from ai_visual_agent.services import asset_intelligence
from ai_visual_agent.services.asset_intelligence import build_project_evidence_context, enrich_project_assets
from ai_visual_agent.services.conversation_agents import run_packaging_strategy_agent, run_usp_agent_result
from ai_visual_agent.services.project_store import project_store
from ai_visual_agent.services.structured_llm import StructuredLLMResult


def test_enrich_project_assets_writes_document_parse_metadata(monkeypatch, tmp_path) -> None:
    doc_path = tmp_path / "product.pptx"
    doc_path.write_bytes(b"fake pptx")
    project = project_store.create(
        ProjectCreateRequest(
            workflow_type="packaging",
            brief=ProjectBrief(category="拼装", core_product_definition="赛博机械宠物"),
            assets=[
                AssetRef(
                    id="doc-1",
                    kind="product_ppt",
                    filename="product.pptx",
                    uri=str(doc_path),
                    mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                )
            ],
        )
    )

    def fake_parse_document_file(**kwargs):
        return {
            "file_id": kwargs["file_id"],
            "parser": "fake-parser",
            "pages": [
                {
                    "page_index": 1,
                    "title": "核心玩法",
                    "text": "简单拼装、发光反馈、可互动机械灵宠。",
                    "semantic_summary": "简单拼装、发光反馈、可互动机械灵宠。",
                }
            ],
        }

    monkeypatch.setattr(asset_intelligence, "parse_document_file", fake_parse_document_file)

    reports = enrich_project_assets(project_id=project.id, workflow_type="packaging")
    updated = project_store.get(project.id)

    assert reports[0]["status"] == "completed"
    assert reports[0]["page_count"] == 1
    assert updated.assets[0].metadata["document_parse"]["parser"] == "fake-parser"
    assert updated.assets[0].metadata["processing"]["status"] == "completed"
    assert updated.assets[0].metadata["processing"]["parser_version"] == "document_parser_v1"
    assert "发光反馈" in build_project_evidence_context(updated)["evidence_digest"]


def test_enrich_project_assets_reuses_completed_project_cache(monkeypatch, tmp_path) -> None:
    doc_path = tmp_path / "product.pptx"
    doc_path.write_bytes(b"fake pptx")
    project = project_store.create(
        ProjectCreateRequest(
            workflow_type="packaging",
            brief=ProjectBrief(category="拼装", core_product_definition="赛博机械宠物"),
            assets=[
                AssetRef(
                    id="doc-cache",
                    kind="product_ppt",
                    filename="product.pptx",
                    uri=str(doc_path),
                    mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    metadata={"sha256": "same-file"},
                )
            ],
        )
    )
    calls = {"count": 0}

    def fake_parse_document_file(**kwargs):
        calls["count"] += 1
        return {
            "file_id": kwargs["file_id"],
            "parser": "fake-parser",
            "pages": [{"page_index": 1, "title": "玩法", "text": "一次解析即可复用。"}],
        }

    monkeypatch.setattr(asset_intelligence, "parse_document_file", fake_parse_document_file)

    first = enrich_project_assets(project_id=project.id, workflow_type="packaging")
    second = enrich_project_assets(project_id=project.id, workflow_type="packaging")

    assert first[0]["status"] == "completed"
    assert second[0]["status"] == "cached"
    assert calls["count"] == 1


def test_enrich_project_assets_parses_untyped_uploaded_ppt(monkeypatch, tmp_path) -> None:
    doc_path = tmp_path / "intro.pptx"
    doc_path.write_bytes(b"fake pptx")
    project = project_store.create(
        ProjectCreateRequest(
            workflow_type="packaging",
            brief=ProjectBrief(category="玩具", core_product_definition="旋转玩具"),
            assets=[
                AssetRef(
                    id="doc-untyped",
                    kind="other",
                    filename="intro.pptx",
                    uri=str(doc_path),
                    mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    metadata={"role_bindings": [{"role": "product_intro", "active": True}]},
                )
            ],
        )
    )

    monkeypatch.setattr(
        asset_intelligence,
        "parse_document_file",
        lambda **kwargs: {
            "file_id": kwargs["file_id"],
            "parser": "fake-parser",
            "pages": [{"page_index": 1, "title": "产品介绍", "text": "多玩法旋转互动。"}],
        },
    )

    reports = enrich_project_assets(project_id=project.id, workflow_type="packaging")
    updated = project_store.get(project.id)

    assert reports[0]["status"] == "completed"
    assert updated.assets[0].metadata["document_parse"]["pages"][0]["text"] == "多玩法旋转互动。"
    assert updated.assets[0].metadata["processing"]["parser_name"] == "document_parser"


def test_usp_agent_receives_document_and_image_evidence(monkeypatch) -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(
            category="拼装",
            target_user="8+ 男孩",
            user_expectations=["拼装成就感", "互动惊喜感"],
            core_product_definition="赛博机械宠物拼装玩具",
        ),
        assets=[
            AssetRef(
                id="doc-1",
                kind="product_ppt",
                filename="product.pptx",
                uri="product.pptx",
                mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                metadata={
                    "document_parse": {
                        "parser": "python-pptx",
                        "pages": [
                            {
                                "page_index": 1,
                                "title": "玩法",
                                "text": "孩子完成拼装后，机械宠物可以发光、响应互动并作为桌面收藏展示。",
                                "semantic_summary": "拼装后可发光、响应互动、桌面收藏展示。",
                            }
                        ],
                    }
                },
            ),
            AssetRef(
                id="image-1",
                kind="product_image",
                filename="hero.png",
                uri="hero.png",
                mime_type="image/png",
                metadata={
                    "image_analysis": {
                        "image_role": "product_image",
                        "semantic_summary": "蓝紫色赛博机械宠物造型，带透明发光部件。",
                        "ocr": {"full_text": ""},
                        "understanding": {
                            "engine": "fake-vlm",
                            "summary": "蓝紫色赛博机械宠物造型。",
                            "product_appearance": ["机械宠物主体", "透明发光部件"],
                            "play_clues": ["拼装完成后可互动展示"],
                        },
                    }
                },
            ),
        ],
    )
    captured = {}

    def fake_invoke_structured(**kwargs):
        captured["context"] = kwargs["context"]
        return StructuredLLMResult(
            output=USPCandidates(
                core=[
                    USPItem(
                        title="拼装后会发光互动的机械灵宠",
                        description="基于 PPT 和产品图证据提炼。",
                        aligned_expectations=["拼装成就感", "互动惊喜感"],
                        product_evidence=["PPT：拼装后可发光、响应互动", "产品图：透明发光部件"],
                        competitor_comparison="当前竞品证据不足，需补充竞品资料。",
                    )
                ]
            ),
            backend="deepseek",
            model="deepseek-v4-pro",
            prompt_name=kwargs["prompt_name"],
            prompt_version="marketer@test",
            prompt_hash="hash",
            output_schema="USPCandidates",
            fallback_used=False,
        )

    monkeypatch.setattr("ai_visual_agent.services.conversation_agents.invoke_structured", fake_invoke_structured)

    result = run_usp_agent_result(project=project, source_message="请提炼卖点")

    evidence = captured["context"]["asset_evidence"]
    assert evidence["parsed_document_count"] == 1
    assert evidence["analyzed_image_count"] == 1
    assert "发光、响应互动" in evidence["evidence_digest"]
    assert result.fallback_used is False
    assert result.output.core[0].title == "拼装后会发光互动的机械灵宠"


def test_packaging_director_receives_asset_evidence(monkeypatch) -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(category="婴童玩具", core_product_definition="转转乐哄娃玩具"),
        assets=[
            AssetRef(
                id="image-1",
                kind="product_image",
                filename="product.png",
                uri="product.png",
                mime_type="image/png",
                metadata={
                    "image_analysis": {
                        "image_role": "product_image",
                        "semantic_summary": "白色四叶旋转结构，蓝黄粉紫配色，中心透明拨珠窗。",
                        "understanding": {
                            "engine": "fake-vlm",
                            "summary": "婴童转转乐玩具。",
                            "product_appearance": ["四叶旋转结构", "中心透明拨珠窗"],
                        },
                    }
                },
            )
        ],
    )
    captured = {}

    def fake_invoke_structured(**kwargs):
        captured["context"] = kwargs["context"]
        captured["fallback"] = kwargs["fallback"]
        return StructuredLLMResult(
            output=PackagingStrategy(
                product_name="转转乐哄娃玩具",
                box_type="开窗盒，保留产品实物识别",
                front_layout="正面展示四叶旋转结构和中心透明拨珠窗。",
            ),
            backend="deepseek",
            model="deepseek-v4-pro",
            prompt_name=kwargs["prompt_name"],
            prompt_version="packaging_director@test",
            prompt_hash="hash",
            output_schema="PackagingStrategy",
            fallback_used=False,
        )

    monkeypatch.setattr("ai_visual_agent.services.conversation_agents.invoke_structured", fake_invoke_structured)

    result = run_packaging_strategy_agent(
        project=project,
        confirmed_usps={"core": [{"title": "一转就有反馈"}]},
        confirmed_vi_profile={"brand_colors": ["white", "purple"]},
    )

    assert captured["context"]["asset_evidence"]["analyzed_image_count"] == 1
    assert "中心透明拨珠窗" in captured["context"]["asset_evidence"]["evidence_digest"]
    assert "欧洲儿童半身模特" in captured["fallback"].front_layout
    assert "触碰感应" in captured["fallback"].front_layout
    assert result.product_name == "转转乐哄娃玩具"
