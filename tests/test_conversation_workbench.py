import time
from uuid import uuid4

from fastapi.testclient import TestClient

from ai_visual_agent.api.dependencies import require_current_user
from ai_visual_agent.domain import (
    AssetRef,
    AuthUser,
    ConversationReviewGate,
    GenerationOutput,
    GenerationOutputItem,
    MainImagePromptDraft,
    PackagingStrategy,
    PlannerDecision,
    ProjectBrief,
    ProjectRecord,
    USPCandidates,
    USPItem,
    VIProfile,
)
from ai_visual_agent.main import app
from ai_visual_agent.services.audit_store import audit_store
from ai_visual_agent.services import asset_intelligence
from ai_visual_agent.services.conversation_agents import (
    _design_reference_asset_ids,
    _ensure_usp_minimum,
    _image_generation_reference_asset_ids,
    _product_reference_asset_ids,
)
from ai_visual_agent.services.conversation_service import _agent_for_review_gate, _processing_tool_for_asset, _role_from_text_mention
from ai_visual_agent.services.conversation_store import SqlConversationStore


def _client() -> TestClient:
    owner = f"conversation-test-user-{uuid4()}"
    app.dependency_overrides[require_current_user] = lambda: AuthUser(
        id=owner,
        email=f"{owner}@example.com",
        role="admin",
    )
    return TestClient(app)


def _approve_and_wait(client: TestClient, session_id: str, body: dict, comment: str, expected_type: str | None = None) -> dict:
    gate = body["pending_review_gate"]
    next_body = client.post(
        f"/api/conversations/{session_id}/review-gates/{gate['id']}/actions",
        json={"action": "approve", "comment": comment},
    ).json()
    if expected_type is None:
        return next_body
    for _ in range(20):
        pending = next_body.get("pending_review_gate") or {}
        if pending.get("type") == expected_type:
            return next_body
        time.sleep(0.02)
        next_body = client.get(f"/api/conversations/{session_id}").json()
    return next_body


def _patch_conversation_agents(monkeypatch) -> None:
    def fake_planner(**kwargs):
        return PlannerDecision(
            intent="extract_selling_points",
            next_action="call_agent",
            target_agent="usp_agent",
            need_human_review=True,
            review_gate_type="usp_review",
            message_to_user="I will extract selling points first.",
            state_patch={"workflow_type": "packaging"},
        )

    def fake_usps(**kwargs):
        return USPCandidates(
            core=[
                USPItem(
                    title="Interactive play",
                    description="Core play is easier for users to understand.",
                    aligned_expectations=["fun"],
                    product_evidence=["project brief"],
                    competitor_comparison="Stronger interactive proof than generic competitors.",
                )
            ],
            secondary=[
                USPItem(
                    title="Visual attraction",
                    description="Packaging can highlight the product hero.",
                    aligned_expectations=["visual appeal"],
                    product_evidence=["project brief"],
                    competitor_comparison="Can be calibrated with competitor evidence later.",
                )
            ],
        )

    def fake_packaging_strategy(**kwargs):
        return PackagingStrategy(
            product_name="Interactive Toy Set",
            box_type="window box",
            front_ratio="1:1",
            side_ratio="box dependent",
            top_ratio="box dependent",
            overall_tone="bright and clear",
            front_layout="Front highlights the product hero and interactive play.",
            left_layout="Left side shows play steps.",
            right_layout="Right side shows accessories.",
            back_layout="Back shows accessories and safety info.",
            required_copy=["Interactive play"],
        )

    def fake_vi(**kwargs):
        return VIProfile(
            brand_colors=["purple", "white"],
            typography_notes="rounded ecommerce type",
            layout_rules=["keep logo clear", "keep product shape"],
            forbidden_rules=["do not change product structure"],
        )

    def fake_image_prompt(**kwargs):
        return MainImagePromptDraft(
            main_image_prompt="Generate a packaging hero image using product references.",
            negative_prompt="Do not change product structure.",
            reference_usage="Product image locks appearance; logo is used for brand area.",
            layout_notes="Front hero composition.",
            text_overlay_plan=["Interactive play"],
        )

    def fake_design(**kwargs):
        return GenerationOutput(
            items=[
                GenerationOutputItem(
                    name="front",
                    asset_id="asset-front",
                    uri="data/assets/asset-front.png",
                    prompt="front packaging",
                    layout_spec={"surface": "front"},
                )
            ],
            revision_round=0,
        )

    monkeypatch.setattr("ai_visual_agent.services.conversation_service.run_planner_agent", fake_planner)
    monkeypatch.setattr("ai_visual_agent.services.conversation_service.run_usp_agent", fake_usps)
    monkeypatch.setattr(
        "ai_visual_agent.services.conversation_service.run_packaging_strategy_agent",
        fake_packaging_strategy,
    )
    monkeypatch.setattr("ai_visual_agent.services.conversation_service.run_vi_understanding_agent", fake_vi)
    monkeypatch.setattr("ai_visual_agent.services.conversation_service.run_packaging_image_prompt_agent", fake_image_prompt)
    monkeypatch.setattr("ai_visual_agent.services.conversation_service.run_design_agent", fake_design)


def test_conversation_create_runs_planner_and_creates_usp_gate(monkeypatch) -> None:
    _patch_conversation_agents(monkeypatch)
    client = _client()

    response = client.post(
        "/api/conversations",
        json={
            "initial_message": (
                "toy packaging project for family users; users care about safety, fun, "
                "and visual appeal; our value proposition is strong interaction."
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["session"]["workflow_type"] == "packaging"
    assert body["pending_review_gate"]["type"] == "usp_review"
    assert body["pending_review_gate"]["payload"]["core"]
    assert any(message["message_type"] == "planner_decision" for message in body["messages"])


def test_conversation_title_prefers_clean_product_name(monkeypatch) -> None:
    _patch_conversation_agents(monkeypatch)
    client = _client()

    response = client.post(
        "/api/conversations",
        json={
            "initial_message": (
                "I want to make packaging for Cyber Mechanical Pet, "
                "target users are children over 8, please extract selling points."
            )
        },
    )

    assert response.status_code == 200
    assert response.json()["session"]["title"] == "Cyber Mechanical Pet"


def test_pending_review_message_regenerates_current_agent_with_revision(monkeypatch) -> None:
    _patch_conversation_agents(monkeypatch)
    captured: dict[str, str] = {}

    def fake_packaging_strategy(**kwargs):
        revision = kwargs.get("revision_request") or ""
        captured["revision_request"] = revision
        return PackagingStrategy(
            product_name="Interactive Toy Set",
            box_type="window box",
            front_ratio="1:1",
            side_ratio="box dependent",
            top_ratio="box dependent",
            overall_tone="bright and clear",
            front_layout=(
                "Second pass: emphasize hand touching and glowing reaction."
                if revision
                else "First pass: Front highlights the product hero."
            ),
            left_layout="Left side shows play steps.",
            right_layout="Right side shows accessories.",
            back_layout="Back shows accessories and safety info.",
            required_copy=["Interactive play"],
        )

    monkeypatch.setattr(
        "ai_visual_agent.services.conversation_service.run_packaging_strategy_agent",
        fake_packaging_strategy,
    )
    monkeypatch.setattr("ai_visual_agent.services.conversation_service._check_dependencies_before_agent", lambda **kwargs: True)
    monkeypatch.setattr("ai_visual_agent.services.conversation_service._ensure_assets_ready_or_schedule", lambda **kwargs: True)
    client = _client()
    created = client.post(
        "/api/conversations",
        json={"initial_message": "toy packaging project, please extract selling points"},
    ).json()
    session_id = created["session"]["id"]
    vi_body = _approve_and_wait(client, session_id, created, "usp ok", "vi_review")
    strategy_body = _approve_and_wait(client, session_id, vi_body, "vi ok", "packaging_strategy_review")
    prompt_body = _approve_and_wait(client, session_id, strategy_body, "strategy ok", "image_prompt_review")
    _approve_and_wait(client, session_id, prompt_body, "prompt ok", "final_design_review")
    design_body = {}
    for _ in range(10):
        design_body = client.get(f"/api/conversations/{session_id}").json()
        if design_body.get("pending_review_gate", {}).get("type") == "final_design_review":
            break
        time.sleep(0.01)
    assert design_body["pending_review_gate"]["type"] == "final_design_review"

    dispatched = []

    def fake_background(**kwargs):
        dispatched.append(kwargs)

    monkeypatch.setattr("ai_visual_agent.services.conversation_service._start_agent_background", fake_background)

    gate_id = design_body["pending_review_gate"]["id"]
    rejected = client.post(
        f"/api/conversations/{session_id}/review-gates/{gate_id}/actions",
        json={"action": "reject", "comment": "閲嶆柊鐢熸垚鍑哄浘"},
    )

    assert rejected.status_code == 200
    assert dispatched[-1]["target_agent"] == "packaging_designer_agent"
    assert any(message["payload"].get("background") for message in rejected.json()["messages"])


def test_packaging_strategy_blocks_without_dimensions(monkeypatch) -> None:
    _patch_conversation_agents(monkeypatch)
    client = _client()
    created = client.post(
        "/api/conversations",
        json={"initial_message": "toy packaging project, extract selling points first."},
    ).json()

    session_id = created["session"]["id"]
    usp_gate = created["pending_review_gate"]["id"]
    vi_body = client.post(
        f"/api/conversations/{session_id}/review-gates/{usp_gate}/actions",
        json={"action": "approve", "comment": "usp ok"},
    ).json()
    vi_gate = vi_body["pending_review_gate"]["id"]
    body = client.post(
        f"/api/conversations/{session_id}/review-gates/{vi_gate}/actions",
        json={"action": "approve", "comment": "vi ok"},
    ).json()

    assert body["pending_review_gate"] is None
    assert any("尺寸" in message["content"] or "size" in message["content"].lower() for message in body["messages"])


def test_empty_real_usp_output_falls_back_to_minimum() -> None:
    fallback = USPCandidates(
        core=[
            USPItem(
                title="淇濆簳鏍稿績鍗栫偣",
                description="Avoid empty cards.",
                aligned_expectations=["fun"],
                product_evidence=["fallback"],
            )
        ],
        secondary=[
            USPItem(
                title="淇濆簳娆¤鍗栫偣",
                description="Avoid empty cards.",
                aligned_expectations=["visual appeal"],
                product_evidence=["fallback"],
            )
        ],
    )

    safe = _ensure_usp_minimum(USPCandidates(), fallback)

    assert safe.core[0].title == "淇濆簳鏍稿績鍗栫偣"
    assert safe.secondary[0].title == "淇濆簳娆¤鍗栫偣"
    assert any("淇濆簳鏍稿績鍗栫偣" in note for note in safe.notes)


def test_design_reference_ids_include_product_logo_and_vi_images() -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(core_product_definition="娴嬭瘯鐜╁叿"),
        assets=[
            AssetRef(id="doc", kind="product_pdf", filename="brief.pdf", uri="brief.pdf", mime_type="application/pdf"),
            AssetRef(
                id="product",
                kind="product_image",
                filename="product.png",
                uri="product.png",
                mime_type="image/png",
                metadata={"asset_memory": {"preferred_product_reference": True}},
            ),
            AssetRef(id="logo", kind="logo", filename="logo.png", uri="logo.png", mime_type="image/png"),
            AssetRef(id="vi-image", kind="vi_document", filename="vi.png", uri="vi.png", mime_type="image/png"),
            AssetRef(id="vi-pdf", kind="vi_document", filename="vi.pdf", uri="vi.pdf", mime_type="application/pdf"),
        ],
    )

    assert _design_reference_asset_ids(project) == ["product", "logo", "vi-image"]
    assert _image_generation_reference_asset_ids(project, {"logo_asset_id": "logo"}) == ["product", "logo"]


def test_design_reference_ids_include_role_bound_untyped_images() -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(core_product_definition="娴嬭瘯鐜╁叿"),
        assets=[
            AssetRef(
                id="product-role",
                kind="other",
                filename="uploaded-product.png",
                uri="uploaded-product.png",
                mime_type="image/png",
                metadata={"role_bindings": [{"role": "product_image", "active": True}]},
            ),
            AssetRef(
                id="logo-role",
                kind="other",
                filename="uploaded-logo.png",
                uri="uploaded-logo.png",
                mime_type="image/png",
                metadata={"role_bindings": [{"role": "logo", "active": True}]},
            ),
        ],
    )

    assert _design_reference_asset_ids(project) == ["product-role", "logo-role"]


def test_product_reference_ids_accept_image_understanding_and_generic_uploads() -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(core_product_definition="测试玩具"),
        assets=[
            AssetRef(
                id="understood-product",
                kind="other",
                filename="赛博灵宠.png",
                uri="赛博灵宠.png",
                mime_type="image/png",
                metadata={
                    "image_analysis": {
                        "image_role": "product_image",
                        "semantic_summary": "用户上传的主体产品参考图",
                    }
                },
            ),
            AssetRef(id="brand-logo", kind="logo", filename="brand-logo.png", uri="brand-logo.png", mime_type="image/png"),
        ],
    )

    assert _product_reference_asset_ids(project) == ["understood-product"]
    assert _design_reference_asset_ids(project) == ["understood-product", "brand-logo"]


def test_product_reference_ids_fall_back_to_untyped_non_brand_images() -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(core_product_definition="测试玩具"),
        assets=[
            AssetRef(id="generic-product", kind="other", filename="赛博灵宠.png", uri="赛博灵宠.png", mime_type="image/png"),
            AssetRef(id="brand-logo", kind="other", filename="logo.png", uri="logo.png", mime_type="image/png"),
        ],
    )

    assert _product_reference_asset_ids(project) == ["generic-product"]
    assert _design_reference_asset_ids(project) == ["generic-product", "brand-logo"]


def test_mention_role_uses_nearest_label_instead_of_earlier_logo_text() -> None:
    content = "品牌logo@topbright.png  产品图@赛博灵宠.png  产品介绍ppt@赛博拼装灵宠（简化版）.pptx"
    logo = AssetRef(id="logo", kind="other", filename="topbright.png", uri="logo.png", mime_type="image/png")
    product = AssetRef(id="product", kind="other", filename="赛博灵宠.png", uri="product.png", mime_type="image/png")
    ppt = AssetRef(id="ppt", kind="other", filename="赛博拼装灵宠（简化版）.pptx", uri="intro.pptx", mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")

    assert _role_from_text_mention(content, logo) == "logo"
    assert _role_from_text_mention(content, product) == "product_image"
    assert _role_from_text_mention(content, ppt) == "product_intro"


def test_product_reference_ids_use_project_text_when_existing_binding_is_wrong() -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(
            core_product_definition="品牌logo@topbright.png  产品图@赛博灵宠.png",
            raw_text="品牌logo@topbright.png  产品图@赛博灵宠.png",
        ),
        assets=[
            AssetRef(
                id="logo",
                kind="other",
                filename="topbright.png",
                uri="logo.png",
                mime_type="image/png",
                metadata={"role_bindings": [{"role": "logo", "active": True}]},
            ),
            AssetRef(
                id="product",
                kind="other",
                filename="赛博灵宠.png",
                uri="product.png",
                mime_type="image/png",
                metadata={"role_bindings": [{"role": "logo", "active": True}]},
            ),
        ],
    )

    assert _product_reference_asset_ids(project) == ["product"]
    assert _design_reference_asset_ids(project) == ["product", "logo"]
    assert _image_generation_reference_asset_ids(project, {"logo_asset_id": "logo"}) == ["product", "logo"]


def test_image_generation_references_exclude_competitor_and_vi_candidates() -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(raw_text="产品图@product.png 产品图@side.png 竞品图@competitor.png 品牌logo@logo.png VI@vi.png"),
        assets=[
            AssetRef(id="product", kind="other", filename="product.png", uri="product.png", mime_type="image/png"),
            AssetRef(id="side", kind="other", filename="side.png", uri="side.png", mime_type="image/png"),
            AssetRef(id="competitor", kind="other", filename="competitor.png", uri="competitor.png", mime_type="image/png"),
            AssetRef(id="logo", kind="other", filename="logo.png", uri="logo.png", mime_type="image/png"),
            AssetRef(id="vi", kind="other", filename="vi.png", uri="vi.png", mime_type="image/png"),
        ],
    )

    assert _image_generation_reference_asset_ids(project, {"logo_asset_id": "logo"}) == ["product", "logo"]


def test_image_generation_references_include_prompt_mentioned_images() -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(raw_text="产品图@product.png 品牌logo@logo.png"),
        assets=[
            AssetRef(id="product", kind="other", filename="product.png", uri="product.png", mime_type="image/png"),
            AssetRef(id="competitor", kind="other", filename="competitor.png", uri="competitor.png", mime_type="image/png"),
            AssetRef(id="logo", kind="other", filename="logo.png", uri="logo.png", mime_type="image/png"),
            AssetRef(
                id="generated",
                kind="other",
                filename="packaging_front_base_r0.png",
                uri="generated.png",
                mime_type="image/png",
                metadata={"asset_role": "generated_visual_base"},
            ),
        ],
    )
    prompt_context = "主图参考：产品图@product.png，竞品图@competitor.png，品牌logo@logo.png。不要使用 packaging_front_base_r0.png。"

    assert _image_generation_reference_asset_ids(
        project,
        {"logo_asset_id": "logo"},
        prompt_context=prompt_context,
    ) == ["product", "competitor", "logo"]


def test_product_reference_ids_accept_prompt_context_labels() -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(raw_text="品牌logo@topbright.png"),
        assets=[
            AssetRef(id="logo", kind="other", filename="topbright.png", uri="logo.png", mime_type="image/png"),
            AssetRef(id="product", kind="other", filename="赛博灵宠.png", uri="product.png", mime_type="image/png"),
        ],
    )

    assert _product_reference_asset_ids(project, prompt_context="产品图@赛博灵宠.png") == ["product"]


def test_image_generation_references_continue_after_competitor_candidate() -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(raw_text="竞品图@WPS图片.jpeg 产品图@WPS图片.png 品牌logo@logo.png"),
        assets=[
            AssetRef(id="competitor", kind="product_image", filename="WPS图片.jpeg", uri="competitor.png", mime_type="image/jpeg"),
            AssetRef(id="product", kind="product_image", filename="WPS图片.png", uri="product.png", mime_type="image/png"),
            AssetRef(id="logo", kind="logo", filename="logo.png", uri="logo.png", mime_type="image/png"),
        ],
    )

    assert _product_reference_asset_ids(project) == ["product"]
    assert _image_generation_reference_asset_ids(project, {"logo_asset_id": "logo"}) == ["product", "logo"]


def test_mention_role_prefers_exact_filename_over_shared_stem() -> None:
    content = "竞品图@WPS图片.jpeg 产品图@WPS图片.png 品牌logo@logo.png"
    competitor = AssetRef(id="competitor", kind="other", filename="WPS图片.jpeg", uri="competitor.png", mime_type="image/jpeg")
    product = AssetRef(id="product", kind="other", filename="WPS图片.png", uri="product.png", mime_type="image/png")

    assert _role_from_text_mention(content, competitor) == "competitor_info"
    assert _role_from_text_mention(content, product) == "product_image"




def _create_packaging_conversation(client: TestClient, *, with_product_image: bool) -> dict:
    created = client.post(
        "/api/conversations",
        json={
            "initial_message": (
                "toy packaging project, product size is 10cm x 8cm x 6cm, "
                "extract selling points first."
            )
        },
    ).json()
    if not with_product_image:
        return created
    session_id = created["session"]["id"]
    upload_response = client.post(
        f"/api/conversations/{session_id}/assets",
        data={"kind": "product_image"},
        files={"file": ("product.png", b"\x89PNG\r\n\x1a\nproduct", "image/png")},
    )
    product_asset = upload_response.json()["assets"][0]
    client.patch(
        f"/api/projects/{created['project']['id']}/assets/{product_asset['id']}",
        json={
            "metadata": {
                "processing": {"status": "completed", "parser_name": "image_understanding", "progress": 100},
                "image_analysis": {"asset_id": product_asset["id"], "semantic_summary": "product image"},
            }
        },
    )
    return created


def _approve_packaging_until_prompt(client: TestClient, created: dict) -> tuple[str, dict]:
    session_id = created["session"]["id"]
    vi_body = _approve_and_wait(client, session_id, created, "usp ok", "vi_review")
    strategy_body = _approve_and_wait(client, session_id, vi_body, "vi ok", "packaging_strategy_review")
    prompt_body = _approve_and_wait(client, session_id, strategy_body, "strategy ok", "image_prompt_review")
    return session_id, prompt_body


def _wait_for_pending_gate(client: TestClient, session_id: str, gate_type: str) -> dict:
    body = client.get(f"/api/conversations/{session_id}").json()
    for _ in range(30):
        pending = body.get("pending_review_gate") or {}
        if pending.get("type") == gate_type:
            return body
        time.sleep(0.02)
        body = client.get(f"/api/conversations/{session_id}").json()
    return body


def test_final_design_generation_reaches_review_gate(monkeypatch) -> None:
    _patch_conversation_agents(monkeypatch)
    client = _client()
    created = _create_packaging_conversation(client, with_product_image=True)
    session_id, prompt_body = _approve_packaging_until_prompt(client, created)

    _approve_and_wait(client, session_id, prompt_body, "prompt ok", "final_design_review")
    body = _wait_for_pending_gate(client, session_id, "final_design_review")

    gate = body["pending_review_gate"]
    assert gate["type"] == "final_design_review"
    assert gate["payload"].get("generation_blocked") is not True
    items = gate["payload"]["generated_outputs"]["items"]
    assert items[0]["name"] == "front"


def test_design_generation_blocks_without_product_reference(monkeypatch) -> None:
    _patch_conversation_agents(monkeypatch)
    client = _client()
    created = _create_packaging_conversation(client, with_product_image=False)
    session_id, prompt_body = _approve_packaging_until_prompt(client, created)

    _approve_and_wait(client, session_id, prompt_body, "prompt ok", "final_design_review")
    body = _wait_for_pending_gate(client, session_id, "final_design_review")

    gate = body["pending_review_gate"]
    assert gate["payload"]["generation_blocked"] is True
    assert gate["payload"]["required_assets"] == ["product_image"]


def test_blocked_design_gate_refreshes_after_product_upload(monkeypatch) -> None:
    _patch_conversation_agents(monkeypatch)
    client = _client()
    created = _create_packaging_conversation(client, with_product_image=False)
    session_id, prompt_body = _approve_packaging_until_prompt(client, created)
    _approve_and_wait(client, session_id, prompt_body, "prompt ok", "final_design_review")
    blocked = _wait_for_pending_gate(client, session_id, "final_design_review")
    assert blocked["pending_review_gate"]["payload"]["generation_blocked"] is True

    upload_response = client.post(
        f"/api/conversations/{session_id}/assets",
        data={"kind": "product_image"},
        files={"file": ("late-product.png", b"\x89PNG\r\n\x1a\nlate", "image/png")},
    )
    product_asset = upload_response.json()["assets"][0]
    client.patch(
        f"/api/projects/{created['project']['id']}/assets/{product_asset['id']}",
        json={"metadata": {"image_analysis": {"asset_id": product_asset["id"], "semantic_summary": "late product"}}},
    )

    refreshed = client.get(f"/api/conversations/{session_id}").json()
    assert refreshed["pending_review_gate"]["payload"].get("generation_blocked") is not True


def test_rejecting_final_design_review_dispatches_background_designer(monkeypatch) -> None:
    _patch_conversation_agents(monkeypatch)
    client = _client()
    created = _create_packaging_conversation(client, with_product_image=True)
    session_id, prompt_body = _approve_packaging_until_prompt(client, created)
    _approve_and_wait(client, session_id, prompt_body, "prompt ok", "final_design_review")
    design_body = _wait_for_pending_gate(client, session_id, "final_design_review")

    dispatched = []

    def fake_background(**kwargs):
        dispatched.append(kwargs)

    monkeypatch.setattr("ai_visual_agent.services.conversation_service._start_agent_background", fake_background)

    gate_id = design_body["pending_review_gate"]["id"]
    rejected = client.post(
        f"/api/conversations/{session_id}/review-gates/{gate_id}/actions",
        json={"action": "reject", "comment": "make it less dark"},
    )

    assert rejected.status_code == 200
    assert dispatched[-1]["target_agent"] == "packaging_designer_agent"
    assert dispatched[-1]["source_message"] == "make it less dark"

def test_delete_conversation_removes_session_project_and_asset_dir(monkeypatch, tmp_path) -> None:
    _patch_conversation_agents(monkeypatch)
    from ai_visual_agent.services.storage import asset_storage

    monkeypatch.setattr(asset_storage, "root", tmp_path / "assets")
    client = _client()
    created = client.post(
        "/api/conversations",
        json={"initial_message": "toy packaging project, extract selling points first."},
    ).json()
    session_id = created["session"]["id"]
    project_id = created["project"]["id"]
    project_dir = asset_storage.root / project_id
    project_dir.mkdir(parents=True)
    (project_dir / "temporary.png").write_bytes(b"temporary")

    delete_response = client.delete(f"/api/conversations/{session_id}")

    assert delete_response.status_code == 204
    assert client.get(f"/api/conversations/{session_id}").status_code == 404
    assert client.get(f"/api/projects/{project_id}").status_code == 404
    assert not project_dir.exists()


def test_conversation_list_recovers_existing_projects() -> None:
    client = _client()
    project = client.post(
        "/api/projects",
        json={
            "workflow_type": "packaging",
            "brief": {
                "category": "baby spinning toy",
                "target_user": "family",
                "value_proposition": "safe interaction",
                "core_product_definition": "baby spinning toy",
            },
            "assets": [],
        },
    ).json()

    response = client.get("/api/conversations")

    assert response.status_code == 200
    body = response.json()
    recovered = next(item for item in body if item["project"]["id"] == project["id"])
    assert recovered["session"]["title"] == "baby spinning toy"
    assert recovered["messages"][0]["payload"]["recovered_from_project_store"] is True


def test_sql_conversation_store_persists_messages_gates_and_confirmed_context(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'conversation.db'}"
    store = SqlConversationStore(database_url)
    store.setup()
    session = store.create_session(project_id="project-1", title="packaging project", workflow_type="packaging")
    message = store.add_message(
        session_id=session.id,
        role="agent",
        message_type="review_gate",
        content="Please confirm USPs.",
        payload={"step": "usp"},
    )
    gate = store.create_review_gate(
        session_id=session.id,
        gate_type="usp_review",
        title="Please confirm core USPs.",
        payload={"core": [{"title": "Interactive"}]},
        next_step_on_approve="vi_understanding_agent",
        created_by_agent="usp_agent",
    )
    store.update_session(session.id, confirmed_context_patch={"confirmed_usps": {"core": [{"title": "Interactive"}]}})

    reopened = SqlConversationStore(database_url)
    reopened.setup()

    persisted_session = reopened.get_session(session.id)
    assert persisted_session.confirmed_context["confirmed_usps"]["core"][0]["title"] == "Interactive"
    assert reopened.list_messages(session.id)[0].id == message.id
    assert reopened.pending_review_gate(session.id).id == gate.id


def test_sql_conversation_store_lists_legacy_blank_owner_for_admin(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'conversation.db'}"
    store = SqlConversationStore(database_url)
    store.setup()
    legacy = store.create_session(project_id="legacy-project", title="legacy useful project", workflow_type="packaging")
    other = store.create_session(
        project_id="other-project",
        owner_id="someone-else@example.com",
        title="other project",
        workflow_type="packaging",
    )
    with store.engine.begin() as conn:
        conn.execute(store._text("UPDATE conversation_sessions SET owner_id = '' WHERE id = :id"), {"id": legacy.id})

    visible = store.list_sessions(owner_id="1173817292@qq.com")

    assert legacy.id in {session.id for session in visible}
    assert other.id not in {session.id for session in visible}


def test_batch_delete_conversations_removes_backend_project_data(monkeypatch, tmp_path) -> None:
    from ai_visual_agent.services.storage import asset_storage

    monkeypatch.setattr(asset_storage, "root", tmp_path / "assets")
    client = _client()
    created = [
        client.post("/api/conversations", json={"title": f"鎵归噺鍒犻櫎椤圭洰 {index}"}).json()
        for index in range(2)
    ]
    session_ids = [item["session"]["id"] for item in created]
    project_ids = [item["project"]["id"] for item in created]
    for project_id in project_ids:
        project_dir = asset_storage.root / project_id
        project_dir.mkdir(parents=True)
        (project_dir / "asset.png").write_bytes(b"asset")
        audit_store.record(project_id=project_id, record_type="agent_output", stage="test", payload={"ok": True})

    response = client.post("/api/conversations/batch-delete", json={"session_ids": session_ids})

    assert response.status_code == 200
    body = response.json()
    assert body["requested_count"] == 2
    assert body["deleted_count"] == 2
    assert body["errors"] == []
    for session_id in session_ids:
        assert client.get(f"/api/conversations/{session_id}").status_code == 404
    for project_id in project_ids:
        assert client.get(f"/api/projects/{project_id}").status_code == 404
        assert not (asset_storage.root / project_id).exists()
        assert audit_store.list_records(project_id) == []
