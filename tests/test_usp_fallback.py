import json
from dataclasses import dataclass

from ai_visual_agent.domain import USPCandidates, USPItem
from ai_visual_agent.graph.nodes import _build_fallback_usp_candidates, _sanitize_usp_candidates
from ai_visual_agent.tools.document_tools import _string_value


def test_usp_fallback_uses_product_evidence_instead_of_generic_copy() -> None:
    candidates = _build_fallback_usp_candidates(
        brief={
            "category": "玩具",
            "target_user": "亲子家庭",
            "user_expectations": ["安全", "好玩", "性价比高"],
            "value_proposition": "不仅是玩具，更是陪伴成长的伙伴",
        },
        parsed_product={
            "play_methods": [
                "Text(pages=[TextPage(page_number=2, text='品名：科学罐头奇迹魔术大剧场\\n"
                "正面卖点文案：15+ 奇迹魔术，365+ 扩展魔术\\n"
                "背面卖点文案：专属魔术舞台，极致表演仪式感\\n"
                "零基础轻松上手，从小玩到大\n"
                "其他外采魔术道具：魔棒变丝带，魔术扑克，魔方还原，硬币穿越')]"
            ],
            "visual_features": [
                "Luxury Cruise Magic Show kit, STEAM educational product, age 8+.",
                "The cruise ship theme works as a theatrical stage for magic performance.",
            ],
            "missing_fields": ["dimensions"],
        },
        competitor_insights={
            "summary": "no competitor assets",
            "competitors": [],
            "opportunity_gaps": ["Clarify the core play proof and make the product subject more readable."],
        },
        memory_notes=[
            {"text": "PPT page: 15+ 奇迹魔术，365+ 扩展魔术；专属魔术舞台；零基础轻松上手。"}
        ],
    )

    dumped = json.dumps(candidates.model_dump(mode="json"), ensure_ascii=False)
    assert "15+ 奇迹魔术" in dumped
    assert "专属魔术舞台" in dumped
    assert "核心体验可视化" not in dumped
    assert "TextPage" not in dumped
    assert "MVP 当前" not in dumped
    assert not any(marker in dumped for marker in ["锛", "鈥", "閰", "鐜", "浜у搧"])
    assert candidates.core[0].product_evidence
    assert "当前竞品证据不足" in candidates.core[0].competitor_comparison


def test_llamaparse_text_object_is_flattened() -> None:
    @dataclass
    class TextPage:
        text: str

    @dataclass
    class Text:
        pages: list[TextPage]

    parsed = _string_value(Text(pages=[TextPage("第一页核心卖点"), TextPage("第二页玩法")]))

    assert parsed == "第一页核心卖点\n第二页玩法"


def test_usp_sanitizer_removes_unsupported_certification_and_new_expectations() -> None:
    candidates = USPCandidates(
        core=[
            USPItem(
                title="STEAM认证，玩出科学思维",
                description="明确标注STEAM认证，满足玩法多样性和教育价值。",
                aligned_expectations=["玩法多样性", "教育价值"],
                product_evidence=["产品图：STEAM教育属性", "产品解析：提及“???????”（推测为教学资源）"],
                competitor_comparison="待补充竞品后验证。",
            )
        ]
    )

    sanitized = _sanitize_usp_candidates(
        candidates,
        brief={"user_expectations": ["安全", "好玩", "性价比高"]},
        parsed_product={"visual_features": ["STEAM教育产品", "8+年龄标识"]},
        memory_notes=[],
    )
    dumped = json.dumps(sanitized.model_dump(mode="json"), ensure_ascii=False)

    assert "认证" not in dumped
    assert "????" not in dumped
    assert "推测" not in dumped
    assert sanitized.core[0].title == "STEAM教育属性，玩出科学思维"
    assert set(sanitized.core[0].aligned_expectations).issubset({"安全", "好玩", "性价比高"})
    assert sanitized.core[0].product_evidence == ["产品图：STEAM教育属性"]
