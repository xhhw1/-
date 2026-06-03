from ai_visual_agent.domain import (
    ProjectBrief,
    ProjectRecord,
    USPCandidates,
    USPCompetitorComparisonRow,
    USPItem,
    USPUserAlignment,
    USPVisualUsage,
)
from ai_visual_agent.services.conversation_agents import run_packaging_strategy_agent, run_usp_agent_result
from ai_visual_agent.services.knowledge_store import build_project_knowledge_context, search_project_knowledge
from ai_visual_agent.services.prompt_registry import get_prompt_registry
from ai_visual_agent.services.structured_llm import StructuredLLMResult


def test_usp_agent_v2_contract_normalizes_visual_selling_point(monkeypatch) -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(
            category="拼装玩具",
            target_user="8+ 男孩",
            user_expectations=["拼装创造感", "互动惊喜感"],
            core_product_definition="赛博机械灵宠",
        ),
    )
    captured = {}

    def fake_invoke_structured(**kwargs):
        captured["instruction"] = kwargs["context"]["instruction"]
        return StructuredLLMResult(
            output=USPCandidates(
                core=[
                    USPItem(
                        headline="亲手拼出一只赛博灵宠",
                        angle="拼装创造感",
                        content="零件简化设计，孩子可以完成一只有科幻感的赛博生命体。",
                        user_alignment=USPUserAlignment(
                            parent="看到孩子展现动手创造力，成品有科技含量。",
                            child="拼装体验不太难，完成后有成就感。",
                        ),
                        product_visual_evidence="散件到完整灵宠的前后对比能形成清晰视觉叙事。",
                        competitor_comparison_rows=[
                            USPCompetitorComparisonRow(
                                dimension="拼装难度",
                                competitor="200+零件，中高难度",
                                our_product="零件简化，低门槛完成",
                            )
                        ],
                        visual_usage=USPVisualUsage(
                            package_headline="亲手拼出一只赛博灵宠",
                            short_tags=["低门槛拼装", "创造生命感"],
                            visual_event="散件经过箭头转化为完整赛博灵宠。",
                            required_visual_elements=["散件", "箭头", "完整灵宠"],
                            recommended_package_area="底部拼装步骤区",
                        ),
                    )
                ],
                secondary=[],
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

    result = run_usp_agent_result(project=project, source_message="请提炼包装卖点")

    item = result.output.core[0]
    assert item.title == "「亲手拼出一只赛博灵宠」——拼装创造感"
    assert item.description == item.content
    assert item.competitor_comparison_rows[0].dimension == "拼装难度"
    assert "散件经过箭头" in item.visual_usage.visual_event
    assert "competitor_comparison_rows" in captured["instruction"]
    assert "visual_usage" in captured["instruction"]


def test_packaging_strategy_fallback_uses_usp_copy_and_auxiliary_visuals(monkeypatch) -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(
            category="拼装玩具",
            target_user="8+ 男孩",
            core_product_definition="赛博机械灵宠",
        ),
    )
    confirmed_usps = {
        "core": [
            {
                "headline": "亲手拼出一只赛博灵宠",
                "visual_usage": {"package_headline": "拼出你的赛博灵宠"},
            }
        ],
        "secondary": [
            {"headline": "10步完成拼装"},
            {"headline": "6款赛博灵宠可收集"},
        ],
    }

    def fake_invoke_structured(**kwargs):
        return StructuredLLMResult(
            output=kwargs["fallback"],
            backend="fallback",
            model="fallback",
            prompt_name=kwargs["prompt_name"],
            prompt_version="packaging_director@test",
            prompt_hash="hash",
            output_schema="PackagingStrategy",
            fallback_used=True,
        )

    monkeypatch.setattr("ai_visual_agent.services.conversation_agents.invoke_structured", fake_invoke_structured)

    result = run_packaging_strategy_agent(
        project=project,
        confirmed_usps=confirmed_usps,
        confirmed_vi_profile={},
    )

    assert "拼出你的赛博灵宠" in result.required_copy
    assert "10步完成拼装" not in result.required_copy
    assert len(result.required_copy) == 1
    assert "核心卖点→画面事件→产品证据→文案层级→信息标识→出图约束" in result.front_layout
    assert "包装构图详解" in result.front_layout
    assert "主卖点文案" in result.front_layout
    assert "辅助产品图" in result.front_layout
    assert "图片参考" in result.left_layout


def test_packaging_director_prompt_saves_visual_strategy_method() -> None:
    prompt = get_prompt_registry().get("packaging_director").content

    assert "核心卖点 → 画面事件 → 产品证据 → 文案层级 → 信息标识 → 出图约束" in prompt
    assert "category_packaging_knowledge" in prompt
    assert "命中的知识条目" in prompt
    assert "策略给清楚方向和边界，但不要把所有构图细节锁死成唯一排版" in prompt
    assert "front_layout` 必须写成报告式" in prompt
    assert "正面主图只允许一处卖点文字表达区" in prompt
    assert "占位型设计文案" in prompt


def test_toy_category_knowledge_matches_packaging_project() -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(
            category="拼装玩具",
            target_user="8+ 儿童",
            core_product_definition="可互动赛博机械宠物",
        ),
    )

    knowledge = search_project_knowledge(project, {"core": [{"headline": "触碰互动"}]})

    assert knowledge
    assert "玩具" in knowledge[0].matched_keywords
    assert knowledge[0].entry.title == "娱乐/玩具类线下包装知识"
    assert "Badge" in str(knowledge[0].entry.content["badge_design"])


def test_packaging_strategy_agent_passes_category_knowledge(monkeypatch) -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(
            category="拼装玩具",
            target_user="8+ 男孩",
            core_product_definition="赛博机械灵宠",
        ),
    )
    confirmed_usps = {
        "core": [{"headline": "触碰它，它就活了"}],
        "secondary": [{"headline": "6款可收集"}],
    }
    captured = {}

    def fake_invoke_structured(**kwargs):
        captured["context"] = kwargs["context"]
        return StructuredLLMResult(
            output=kwargs["fallback"],
            backend="fallback",
            model="fallback",
            prompt_name=kwargs["prompt_name"],
            prompt_version="packaging_director@test",
            prompt_hash="hash",
            output_schema="PackagingStrategy",
            fallback_used=True,
        )

    monkeypatch.setattr("ai_visual_agent.services.conversation_agents.invoke_structured", fake_invoke_structured)

    result = run_packaging_strategy_agent(
        project=project,
        confirmed_usps=confirmed_usps,
        confirmed_vi_profile={},
    )

    category_knowledge = captured["context"]["category_packaging_knowledge"]
    assert category_knowledge["matched"] is True
    assert "知识库" in captured["context"]["instruction"]
    assert "Badge" in str(category_knowledge["results"][0]["content"]["badge_design"])
    assert "知识库" in result.front_layout


def test_project_knowledge_context_is_database_backed() -> None:
    project = ProjectRecord(
        workflow_type="packaging",
        brief=ProjectBrief(
            category="拼装玩具",
            target_user="8+ 儿童",
            core_product_definition="可互动赛博机械宠物",
        ),
    )

    context = build_project_knowledge_context(project, {"core": [{"headline": "触碰互动"}]})

    assert context["matched"] is True
    assert context["results"][0]["id"] == "toy_entertainment_offline_packaging_v1"
    assert "知识库" in context["instruction"]
