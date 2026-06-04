from __future__ import annotations

import json
from typing import Any

from ai_visual_agent.domain import (
    DetailPageStrategy,
    DetailScreenStrategy,
    GenerationOutput,
    MainImagePromptDraft,
    PackagingStrategy,
    PlannerDecision,
    ProjectRecord,
    USPCandidates,
    USPCompetitorComparisonRow,
    USPItem,
    USPUserAlignment,
    USPVisualUsage,
    VIProfile,
)
from ai_visual_agent.services.asset_intelligence import build_project_evidence_context, compact_evidence_for_downstream
from ai_visual_agent.services.design_generation import generate_design_outputs
from ai_visual_agent.services.knowledge_store import build_project_knowledge_context
from ai_visual_agent.services.structured_llm import StructuredLLMResult, invoke_structured


def run_planner_agent(
    *,
    user_message: str,
    session: Any,
    project: ProjectRecord,
    messages: list[Any],
    pending_review_gate: Any | None,
) -> PlannerDecision:
    fallback = _fallback_planner_decision(
        user_message=user_message,
        workflow_type=session.workflow_type,
        has_confirmed_usps=bool(session.confirmed_context.get("confirmed_usps")),
        has_confirmed_vi_profile=bool(session.confirmed_context.get("confirmed_vi_profile")),
        has_confirmed_strategy=bool(
            session.confirmed_context.get("confirmed_detail_page_strategy")
            if session.workflow_type == "detail_page"
            else session.confirmed_context.get("confirmed_packaging_strategy")
        ),
        pending_review_gate_type=getattr(pending_review_gate, "type", ""),
    )
    result = invoke_structured(
        schema=PlannerDecision,
        prompt_name="planner_agent",
        context={
            "latest_user_message": user_message,
            "session": session.model_dump(mode="json"),
            "project": project.model_dump(mode="json"),
            "recent_messages": [message.model_dump(mode="json") for message in messages[-8:]],
            "pending_review_gate": pending_review_gate.model_dump(mode="json") if pending_review_gate else None,
        },
        fallback=fallback,
        model_role="fast",
    )
    return result.output


def run_usp_agent(*, project: ProjectRecord, source_message: str) -> USPCandidates:
    return run_usp_agent_result(project=project, source_message=source_message).output


def run_usp_agent_result(*, project: ProjectRecord, source_message: str) -> StructuredLLMResult[USPCandidates]:
    fallback = _fallback_usps(project=project, source_message=source_message)
    asset_evidence = build_project_evidence_context(project)
    result = invoke_structured(
        schema=USPCandidates,
        prompt_name="marketer",
        context={
            "project_brief": project.brief.model_dump(mode="json"),
            "source_message": source_message,
            "assets": _asset_index(project),
            "asset_evidence": asset_evidence,
            "instruction": (
                "输出 1-3 条核心卖点和 1-3 条次要卖点。每条核心卖点必须采用"
                "「一句可上包装的短文案」——卖点角度 的 title 结构，并补齐 headline、angle、"
                "content、父母/孩子 user_alignment、product_visual_evidence、竞品对比表 "
                "competitor_comparison_rows、competitiveness_judgement 和 visual_usage。"
                "必须引用 asset_evidence 中的 PPT/PDF 页面、产品图理解、OCR 或用户简报证据；"
                "如果证据不足，必须降低置信度并在 notes 中说明，不能把待确认信息写成确定卖点。"
            ),
        },
        fallback=fallback,
        model_role="strategy",
    )
    output = _ensure_usp_minimum(result.output, fallback)
    if result.fallback_used:
        output.notes.append(f"模型调用未成功返回结构化卖点，已启用保底结果：{result.error or 'unknown error'}")
    if asset_evidence.get("missing_or_failed_assets"):
        output.notes.append("仍有资料未完成解析/理解，请在人工审核时确认是否需要补充或重新分析。")
    return StructuredLLMResult(
        output=output,
        backend=result.backend,
        model=result.model,
        prompt_name=result.prompt_name,
        prompt_version=result.prompt_version,
        prompt_hash=result.prompt_hash,
        output_schema=result.output_schema,
        fallback_used=result.fallback_used,
        error=result.error,
    )


def _usp_packaging_headlines(confirmed_usps: dict[str, Any], group: str) -> list[str]:
    copies: list[str] = []
    for item in confirmed_usps.get(group, []) or []:
        if not isinstance(item, dict):
            continue
        visual_usage = item.get("visual_usage") if isinstance(item.get("visual_usage"), dict) else {}
        candidates = [
            visual_usage.get("package_headline") if visual_usage else "",
            item.get("headline"),
            item.get("title"),
            item.get("content"),
            item.get("description"),
        ]
        for candidate in candidates:
            copy = _clean_package_copy(candidate)
            if copy:
                copies.append(copy)
                break
    return copies


def _clean_package_copy(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "「" in text and "」" in text:
        text = text.split("「", 1)[1].split("」", 1)[0].strip()
    if "——" in text:
        text = text.split("——", 1)[0].strip()
    return text[:24]


def run_packaging_strategy_agent(
    *,
    project: ProjectRecord,
    confirmed_usps: dict[str, Any],
    confirmed_vi_profile: dict[str, Any],
    revision_request: str = "",
) -> PackagingStrategy:
    core_titles = _usp_packaging_headlines(confirmed_usps, "core")
    secondary_titles = _usp_packaging_headlines(confirmed_usps, "secondary")
    main_copy = core_titles[0] if core_titles else "核心卖点一眼看懂"
    category_knowledge = build_project_knowledge_context(project, confirmed_usps, domain="packaging")
    knowledge_instruction = str(category_knowledge.get("instruction") or "")
    fallback = PackagingStrategy(
        product_name=project.brief.core_product_definition or project.brief.category or "产品",
        box_type="优先选择能展示产品实物或核心形态的开窗盒/天地盖；具体刀模和开窗范围需结合产品尺寸确认",
        front_ratio="正面建议 1:1 或 4:5，保证产品主视觉和唯一卖点表达区都有足够阅读空间",
        side_ratio="按实际盒型侧边比例配置",
        top_ratio="按实际盒型顶面比例配置",
        overall_tone="先把核心卖点转成可见的画面事件，再用 VI 色、光影和信息层级提升包装吸引力；视觉提升不能改变产品外观、结构、颜色和配件数量。",
        front_layout=(
            "包装构图详解\n\n"
            "产品核心图展示：按「核心卖点→画面事件→产品证据→文案层级→信息标识→出图约束」组织正面策略。"
            f"主标题建议使用“{main_copy}”，将第一核心卖点转成一个一眼能看见的画面事件，例如产品完成状态、互动瞬间、拼装前后变化或系列展示。"
            "产品参考图必须作为主视觉证据，主体可适度放大并强化光影质感，但不得改变产品外观、颜色、结构和配件数量。\n\n"
            "辅助产品图展示：次要卖点必须由辅助图承载，而不是在多个位置重复写卖点。可在边角规划拼装步骤、爆炸散件图、局部功能特写、配件小图或系列款缩略图，"
            "尽量用图形关系证明卖点，不再增加第二处卖点文字。\n\n"
            "背景图与背景色：背景需要服务产品世界观和用户感受，不是泛泛的高级感。可根据产品主题使用场景氛围、科技光效、柔和地台、纹理层次或 VI 辅助色，"
            "确保产品主体清晰、有空间支撑，不出现悬浮廉价感。\n\n"
            "模特或手部融入方式：只有当人物能证明互动、尺寸或情绪价值时才加入。对于儿童玩具、触碰感应、发光反馈或拼装成就类产品，"
            "优先采用欧洲儿童半身模特位于产品右侧的互动证明构图：孩子身体微微前倾，瞪大眼睛、张嘴惊喜，视线完全被手中正在发光的产品吸引；"
            "一只手托住产品底座或主体稳定区域，另一只手食指轻轻触碰感应圆球、触控区或核心互动部件；指尖与触碰点迸发明亮能量光环和粒子特效。"
            "该构图必须同时证明触碰互动方式、暗示产品实物尺寸，并把交互惊喜感传递给货架前消费者。人物不能抢走产品主体。\n\n"
            "主卖点文案：正面只保留一处卖点表达区，建议使用一个主标题或一句短标语；不要再安排卖点副标题、底部卖点条、多个卖点徽章或带卖点文案的功能图标。\n\n"
            "辅助标识：LOGO、品名、年龄标识、系列编号等只承担识别作用，不重复卖点。正面主图不放认证标识区、安全提示区、条码区或厂商信息区，这些内容只进入背面策略。"
            + (
                " 如命中知识库，需把其中的品类原则、首选设计风格、徽章层级和风险边界转成当前产品可执行的包装策略。"
                if category_knowledge.get("matched")
                else ""
            )
        ),
        left_layout=f"展示核心玩法步骤或使用场景，图片参考优先使用产品步骤图/手部互动图，配短句说明“{secondary_titles[0] if secondary_titles else '玩法一眼看懂'}”。",
        right_layout=f"展示配件、尺寸、系列收集或第二玩法，图片参考优先使用配件图/系列图/局部特写，配短句说明“{secondary_titles[1] if len(secondary_titles) > 1 else '更多玩法可探索'}”。",
        back_layout="配件清单、玩法说明、安全提示、条码占位区和厂商信息区需要输出具体文案草案与版式分区；真实认证号、条码号、检测编号如无资料，仅以占位设计呈现。",
        required_copy=[main_copy],
        required_icons=["年龄角标", "系列编号", "无文字功能符号", "辅助图入口"],
        risk_notes=["必须遵守已确认 VI；没有资料证据的真实认证号、条码号、检测编号和厂商全称只能作为占位型设计草案，不能伪造成真实信息。"],
    )
    result = invoke_structured(
        schema=PackagingStrategy,
        prompt_name="packaging_director",
        context={
            "project_brief": project.brief.model_dump(mode="json"),
            "selected_usps": confirmed_usps,
            "confirmed_vi_profile": confirmed_vi_profile,
            "asset_evidence": compact_evidence_for_downstream(build_project_evidence_context(project)),
            "assets": _asset_index(project),
            "category_packaging_knowledge": category_knowledge,
            "revision_request": revision_request,
            "instruction": (
                "包装策略必须按「核心卖点→画面事件→产品证据→文案层级→信息标识→出图约束」输出。"
                " 先把卖点动态化表达成画面事件，再指定产品证据和辅助图参考；策略要具体但不能把构图锁死成唯一施工图。"
                " 如果 category_packaging_knowledge.matched 为 true，必须自主判断命中的知识条目是否适用于当前产品，"
                "并将适用原则转译为具体策略；如果不适用，说明原因并写入 risk_notes。"
                f" {knowledge_instruction}"
                + (
                    " 本轮是根据用户对上一版包装策略的修正意见重新输出，必须优先回应 revision_request。"
                    if revision_request
                    else ""
                )
            ),
        },
        fallback=fallback,
        model_role="strategy",
    )
    return result.output


def run_detail_strategy_agent(
    *,
    project: ProjectRecord,
    confirmed_usps: dict[str, Any],
    confirmed_vi_profile: dict[str, Any],
    revision_request: str = "",
) -> DetailPageStrategy:
    core_titles = _usp_packaging_headlines(confirmed_usps, "core")
    main_copy = core_titles[0] if core_titles else "核心卖点一屏讲清"
    fallback = DetailPageStrategy(
        page_theme=f"{project.brief.category or '产品'}详情页提案",
        screens=[
            DetailScreenStrategy(
                screen_index=1,
                goal="建立第一眼吸引力",
                visual="大产品主体 + 场景氛围 + 主卖点标题",
                copy_text=main_copy,
                product_angle="最能体现吸引力和竞争力的产品角度",
            ),
            DetailScreenStrategy(
                screen_index=2,
                goal="放大核心功能或玩法",
                visual="功能特写或玩法拆解",
                copy_text="把核心玩法说具体",
                proof_points=["玩法动作", "用户收益"],
            ),
            DetailScreenStrategy(
                screen_index=3,
                goal="建立竞品差异化",
                visual="对比式信息结构或场景证明",
                copy_text="为什么选择我们",
            ),
            DetailScreenStrategy(
                screen_index=4,
                goal="补充配件、尺寸、材质和细节",
                visual="配件平铺 + 尺寸标注 + 细节放大",
                copy_text="买前关键信息完整呈现",
            ),
            DetailScreenStrategy(
                screen_index=5,
                goal="形成购买闭环",
                visual="套装完整展示 + 信任背书",
                copy_text="适合目标用户的完整解决方案",
            ),
        ],
        traffic_platform_notes="首屏兼顾点击，后续屏幕服务停留、理解和转化。",
        risk_notes=["必须遵守已确认 VI；详情页文字建议程序化排版。"],
    )
    result = invoke_structured(
        schema=DetailPageStrategy,
        prompt_name="detail_page_director",
        context={
            "project_brief": project.brief.model_dump(mode="json"),
            "selected_usps": confirmed_usps,
            "confirmed_vi_profile": confirmed_vi_profile,
            "assets": _asset_index(project),
            "asset_evidence": compact_evidence_for_downstream(build_project_evidence_context(project)),
            "revision_request": revision_request,
            "instruction": (
                "详情页策略必须先吸收已确认的品牌色、LOGO 使用、版式禁忌和产品一致性约束，再输出五屏方案。"
                + (
                    " 本轮是根据用户对上一版详情页策略的修正意见重新输出，必须优先回应 revision_request。"
                    if revision_request
                    else ""
                )
            ),
        },
        fallback=fallback,
        model_role="strategy",
    )
    return result.output


def run_packaging_image_prompt_agent(
    *,
    project: ProjectRecord,
    confirmed_usps: dict[str, Any],
    confirmed_vi_profile: dict[str, Any],
    packaging_strategy: dict[str, Any],
    revision_request: str = "",
) -> MainImagePromptDraft:
    reference_ids = _design_reference_asset_ids(project)
    assets_by_id = {asset.id: asset for asset in project.assets}
    reference_assets = _asset_index_from_assets([assets_by_id[asset_id] for asset_id in reference_ids if asset_id in assets_by_id])
    core_titles = _usp_packaging_headlines(confirmed_usps, "core")
    secondary_titles = _usp_packaging_headlines(confirmed_usps, "secondary")
    raw_copy_points = packaging_strategy.get("required_copy") or core_titles or secondary_titles
    if not isinstance(raw_copy_points, list):
        raw_copy_points = [raw_copy_points]
    copy_points = raw_copy_points[:1]
    colors = confirmed_vi_profile.get("brand_colors") or []
    logo_asset_id = confirmed_vi_profile.get("logo_asset_id")
    logo_rule = (
        f"参考已上传 LOGO 素材 {logo_asset_id}，把 LOGO 融入包装正面合适位置并保持识别清晰。"
        if logo_asset_id
        else "未提供可确认 LOGO 时，可以设计品牌识别区域和装饰性标志位，但不要虚构具体品牌标志。"
    )
    main_prompt = "\n".join(
        part
        for part in [
            "生成一张电商包装主图 / 包装正面视觉设计图，商业级产品包装渲染质感。",
            "必须以用户上传的产品参考图为准，保持产品外观、颜色、结构、比例、配件数量和核心形态完全一致。",
            f"产品名称上下文：{packaging_strategy.get('product_name') or project.brief.core_product_definition or project.brief.category or '产品'}。",
            f"主图设计方案（优先按此执行）：{packaging_strategy.get('front_layout') or '产品主体作为画面主角，只保留一处主卖点表达区，辅助产品图用图形承载，不重复写卖点。'}",
            "如果主图设计方案中包含模特或手部互动，必须保留互动证明逻辑：欧洲儿童半身入画位于产品右侧，身体微微前倾，瞪大眼睛、张嘴惊喜，视线被发光产品吸引；一只手托住产品底座，另一只手食指轻触感应圆球/触控区/核心互动部件，触碰点产生能量光环、粒子或发光反馈，用来证明互动方式、产品尺寸和惊喜情绪。",
            f"整体影调：{packaging_strategy.get('overall_tone') or '干净、明亮、适合电商展示，产品主体清晰。'}",
            f"VI 颜色使用：{', '.join(str(item) for item in colors if item)}。" if colors else "VI 颜色未明确时，使用干净温和、不抢产品主体的辅助色。",
            logo_rule,
            f"本轮用户修正要求：{revision_request}" if revision_request else "",
            f"画面上只允许出现这一处主卖点短文案：{', '.join(str(item) for item in copy_points if str(item).strip())}。" if copy_points else "",
            "不要在副标题、底部条、多个卖点徽章或功能图标文字中重复卖点；功能图标尽量无文字或只承担年龄/系列识别。",
            "次要卖点必须有辅助图片参考或小图承载，例如拼装步骤图、产品局部特写、配件小图、系列款缩略图或简洁示意图，但不要增加第二处卖点文字。",
            "正面主图不生成认证标识区、安全提示区、条码占位区或厂商信息区。",
            "画面应有柔和商业摄影棚光影、干净背景、清晰空间层次，适合电商包装审核和进一步精修。",
        ]
        if str(part).strip()
    )
    fallback = MainImagePromptDraft(
        main_image_prompt=main_prompt,
        negative_prompt=(
            "不要改变产品结构、颜色、比例、配件数量；不要添加不存在的功能或配件；"
            "不要虚构未提供的品牌 LOGO；正面主图不要出现认证标识区、安全提示区、条码占位区、厂商信息区；不要重复生成多处卖点文字。"
        ),
        reference_usage="优先使用产品图/透明产品图锁定产品外观；LOGO 与 VI 图片用于品牌色、标志位置、标签风格和版式约束参考。",
        layout_notes=str(packaging_strategy.get("front_layout") or ""),
        text_overlay_plan=[
            str(item)
            for item in copy_points
            if str(item).strip()
        ][:6],
        risk_notes=[
            "这是提示词预览节点；确认后才会调用出图 Agent。",
            "主图只保留一处卖点表达；合规认证、安全提示、条码和厂商信息不进入正面主图。",
        ],
    )
    result = invoke_structured(
        schema=MainImagePromptDraft,
        prompt_name="packaging_image_prompt_writer",
        context={
            "project_brief": project.brief.model_dump(mode="json"),
            "selected_usps": confirmed_usps,
            "confirmed_vi_profile": confirmed_vi_profile,
            "packaging_strategy": packaging_strategy,
            "asset_evidence": compact_evidence_for_downstream(build_project_evidence_context(project)),
            "reference_assets": reference_assets,
            "revision_request": revision_request,
            "instruction": (
                "只生成包装主图/正面视觉图的 GPT-Image-2 图生图提示词，不生成侧面或背面提示词。"
                + (
                    " 本轮是根据用户对上一版提示词的修正意见重新输出，必须优先回应 revision_request。"
                    if revision_request
                    else ""
                )
            ),
        },
        fallback=fallback,
        model_role="strategy",
    )
    output = result.output
    if not output.main_image_prompt.strip():
        output = fallback
        output.risk_notes.append("模型返回的主图提示词为空，已切换为保底提示词。")
    if result.fallback_used:
        output.risk_notes.append(f"模型调用未成功返回主图提示词，已启用保底提示词：{result.error or 'unknown error'}")
    return output


def run_vi_understanding_agent(
    *,
    project: ProjectRecord,
    confirmed_strategy: dict[str, Any],
    confirmed_usps: dict[str, Any] | None = None,
    workflow_type: str,
    revision_request: str = "",
) -> VIProfile:
    vi_assets = [
        asset
        for asset in project.assets
        if asset.kind in {"vi_document", "logo"} or _asset_has_role(asset, {"vi_reference", "logo"})
    ]
    logo = next((asset for asset in vi_assets if asset.kind == "logo" or _asset_has_role(asset, {"logo"})), None)
    fallback = VIProfile(
        brand_colors=["\u5f85\u4ece VI \u6587\u6863\u6216 LOGO \u4e2d\u63d0\u53d6"],
        logo_asset_id=logo.id if logo else None,
        typography_notes="\u5982\u672a\u4e0a\u4f20 VI\uff0c\u5148\u91c7\u7528\u5706\u6da6\u3001\u6613\u8bfb\u3001\u7535\u5546\u5305\u88c5\u53cb\u597d\u7684\u5b57\u4f53\u5c42\u7ea7\u3002",
        layout_rules=[
            "\u4fdd\u7559 LOGO\u3001\u54c1\u540d\u3001\u6838\u5fc3\u5356\u70b9\u548c\u4ea7\u54c1\u4e3b\u4f53\u7684\u6e05\u6670\u5c42\u7ea7\u3002",
            "\u5305\u88c5\u7b56\u7565\u548c\u51fa\u56fe\u5fc5\u987b\u5148\u5bf9\u9f50\u5df2\u786e\u8ba4\u7684\u5356\u70b9\u4e0e\u54c1\u724c\u8bc6\u522b\u89c4\u8303\u3002",
            "\u5305\u88c5\u56fe\u53ef\u505a\u5149\u5f71\u548c\u8d28\u611f\u63d0\u5347\uff0c\u4f46\u4e0d\u6539\u53d8\u4ea7\u54c1\u5916\u89c2\u3001\u914d\u4ef6\u6570\u91cf\u548c\u6838\u5fc3\u7ed3\u6784\u3002",
        ],
        forbidden_rules=[
            "\u4e0d\u5f97\u865a\u6784\u672a\u7ecf\u8bc1\u5b9e\u7684\u8ba4\u8bc1\u3001\u6750\u8d28\u6216\u529f\u80fd\u3002",
            "\u4e0d\u5f97\u8ba9\u4ea7\u54c1\u5f62\u6001\u3001\u989c\u8272\u3001\u5438\u76d8\u6216\u73a9\u6cd5\u7ec4\u4ef6\u4e0e\u8d44\u6599\u4e0d\u4e00\u81f4\u3002",
        ],
        source_asset_ids=[asset.id for asset in vi_assets],
    )
    result = invoke_structured(
        schema=VIProfile,
        prompt_name="vi_guardian",
        context={
            "workflow_type": workflow_type,
            "project_brief": project.brief.model_dump(mode="json"),
            "selected_usps": confirmed_usps or {},
            "confirmed_strategy": confirmed_strategy,
            "vi_assets": _asset_index_from_assets(vi_assets),
            "all_assets": _asset_index(project),
            "revision_request": revision_request,
            "instruction": (
                "You are the VI understanding agent. Extract brand colors, logo usage, typography, layout rules, "
                "and forbidden rules. If VI evidence is missing, mark assumptions clearly and keep product consistency first."
                + (
                    " This is a revision pass based on the user's feedback on the previous VI result; prioritize revision_request."
                    if revision_request
                    else ""
                )
            ),
        },
        fallback=fallback,
        model_role="fast",
    )
    return result.output


def run_design_agent(
    *,
    project: ProjectRecord,
    workflow_type: str,
    confirmed_strategy: dict[str, Any],
    confirmed_vi_profile: dict[str, Any],
    confirmed_image_prompt: dict[str, Any] | None = None,
    revision_request: str = "",
    reference_prompt_context: str = "",
    return_partial_on_error: bool = False,
    on_item_generated: Any | None = None,
    on_generation_error: Any | None = None,
) -> GenerationOutput:
    image_prompt = dict(confirmed_image_prompt or {}) if confirmed_image_prompt else None
    if image_prompt is not None and revision_request:
        existing_prompt = str(image_prompt.get("main_image_prompt") or "")
        image_prompt["main_image_prompt"] = (
            existing_prompt.rstrip()
            + "\n\n本轮用户修正要求："
            + revision_request
            + "\n请在保持产品参考图一致性的前提下，优先满足上述修正。"
        )
        risk_notes = image_prompt.get("risk_notes")
        if not isinstance(risk_notes, list):
            risk_notes = []
        risk_notes.append("本轮出图已合并用户自然语言修正意见。")
        image_prompt["risk_notes"] = risk_notes
    prompt_context = _image_reference_prompt_context(
        confirmed_strategy=confirmed_strategy,
        confirmed_image_prompt=image_prompt,
        revision_request=revision_request,
        extra_context=reference_prompt_context,
    )
    return generate_design_outputs(
        project_id=project.id,
        workflow_type="detail_page" if workflow_type == "detail_page" else "packaging",
        strategy=confirmed_strategy,
        vi_profile=confirmed_vi_profile,
        revision_round=0,
        reference_asset_ids=_image_generation_reference_asset_ids(
            project,
            confirmed_vi_profile,
            prompt_context=prompt_context,
        ),
        main_image_prompt_draft=image_prompt,
        allow_mock_fallback=False,
        return_partial_on_error=return_partial_on_error,
        on_item_generated=on_item_generated,
        on_generation_error=on_generation_error,
    )


def _product_reference_asset_ids(project: ProjectRecord, *, prompt_context: str = "") -> list[str]:
    prompt_product_ids: list[str] = []
    for asset_id in _prompt_mentioned_image_asset_ids(project, prompt_context):
        try:
            asset = _asset_by_id(project, asset_id)
        except StopIteration:
            continue
        context_role = _asset_role_from_text(prompt_context, asset)
        if _is_product_reference_asset(asset, text_role=context_role or _project_text_role(project, asset)):
            prompt_product_ids.append(asset_id)
    if prompt_product_ids:
        return prompt_product_ids[:3]
    reference_ids = _design_reference_asset_ids(project)
    explicit_ids = [
        asset_id
        for asset_id in reference_ids
        if _is_product_reference_asset(_asset_by_id(project, asset_id), text_role=_project_text_role(project, _asset_by_id(project, asset_id)))
    ]
    if explicit_ids:
        return explicit_ids
    return [asset.id for asset in _generic_product_reference_candidates(project)[:3]]


def _design_reference_asset_ids(project: ProjectRecord) -> list[str]:
    candidates: list[tuple[int, int, str]] = []
    for index, asset in enumerate(project.assets):
        if not _is_image_asset(asset):
            continue
        metadata = asset.metadata or {}
        memory = metadata.get("asset_memory") if isinstance(metadata.get("asset_memory"), dict) else {}
        text_role = _project_text_role(project, asset)
        priority: int | None = None
        if _is_product_reference_asset(asset, text_role=text_role):
            priority = 1
        elif _is_logo_reference_asset(asset, text_role=text_role):
            priority = 2
        elif _is_vi_reference_asset(asset, text_role=text_role):
            priority = 3
        elif _is_generic_product_reference_candidate(asset, text_role=text_role):
            priority = 1
        else:
            continue
        if _is_product_reference_asset(asset, text_role=text_role) and memory.get("preferred_product_reference"):
            priority = 0
        candidates.append((priority, index, asset.id))
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for _, _, asset_id in sorted(candidates):
        if asset_id in seen:
            continue
        ordered_ids.append(asset_id)
        seen.add(asset_id)
    return ordered_ids[:8]


def _image_generation_reference_asset_ids(
    project: ProjectRecord,
    vi_profile: dict[str, Any] | None = None,
    *,
    prompt_context: str = "",
) -> list[str]:
    """References that are actually eligible to be sent to the image API.

    Strategy and prompt agents can inspect VI or competitor images as context, but the
    image API should receive only product-locking references, explicitly mentioned
    prompt references, plus a real uploaded logo. This keeps generation traceable and
    avoids leaking unused candidate images into the final "reference images" UI.
    """
    ids: list[str] = []
    seen: set[str] = set()

    def add(asset_id: str) -> None:
        if asset_id and asset_id not in seen:
            ids.append(asset_id)
            seen.add(asset_id)

    for asset_id in _product_reference_asset_ids(project, prompt_context=prompt_context):
        asset = _asset_by_id(project, asset_id)
        text_role = _project_text_role(project, asset)
        if text_role != "product_image" and (
            _is_competitor_reference_asset(asset)
            or _is_logo_reference_asset(asset)
            or _is_vi_reference_asset(asset)
        ):
            continue
        add(asset_id)
        break

    mentioned_product_ids: list[str] = []
    mentioned_other_ids: list[str] = []
    for asset_id in _prompt_mentioned_image_asset_ids(project, prompt_context):
        try:
            asset = _asset_by_id(project, asset_id)
        except StopIteration:
            continue
        context_role = _asset_role_from_text(prompt_context, asset)
        text_role = context_role or _project_text_role(project, asset)
        if _is_product_reference_asset(asset, text_role=text_role):
            mentioned_product_ids.append(asset_id)
        else:
            mentioned_other_ids.append(asset_id)
    for asset_id in mentioned_product_ids:
        add(asset_id)
    for asset_id in mentioned_other_ids:
        add(asset_id)

    logo_id = ""
    if isinstance(vi_profile, dict):
        logo_id = str(vi_profile.get("logo_asset_id") or "")
    logo_candidates = [logo_id] if logo_id else []
    logo_candidates.extend(
        asset.id
        for asset in project.assets
        if _is_image_asset(asset)
        and _project_text_role(project, asset) != "product_image"
        and _is_logo_reference_asset(asset, text_role=_project_text_role(project, asset))
    )
    for asset_id in logo_candidates:
        if not asset_id or asset_id in ids:
            continue
        try:
            asset = _asset_by_id(project, asset_id)
        except StopIteration:
            continue
        text_role = _project_text_role(project, asset)
        if _is_image_asset(asset) and text_role != "product_image" and _is_logo_reference_asset(asset, text_role=text_role):
            add(asset_id)
            break

    return ids[:4]


def _image_reference_prompt_context(
    *,
    confirmed_strategy: dict[str, Any],
    confirmed_image_prompt: dict[str, Any] | None = None,
    revision_request: str = "",
    extra_context: str = "",
) -> str:
    parts: list[str] = []
    for value in [
        confirmed_image_prompt,
        confirmed_strategy,
        revision_request,
        extra_context,
    ]:
        if not value:
            continue
        if isinstance(value, str):
            parts.append(value)
        else:
            try:
                parts.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
            except TypeError:
                parts.append(str(value))
    return "\n".join(parts)


def _prompt_mentioned_image_asset_ids(project: ProjectRecord, prompt_context: str) -> list[str]:
    if not prompt_context:
        return []
    mentions: list[tuple[int, int, str]] = []
    for index, asset in enumerate(project.assets):
        if not _is_image_asset(asset) or _is_generated_image_asset(asset):
            continue
        positions = _asset_positions_in_text(prompt_context, asset)
        if positions:
            mentions.append((positions[0], index, asset.id))
    ordered: list[str] = []
    seen: set[str] = set()
    for _, _, asset_id in sorted(mentions):
        if asset_id in seen:
            continue
        ordered.append(asset_id)
        seen.add(asset_id)
    return ordered


def _asset_positions_in_text(text: str, asset: Any) -> list[int]:
    if not text:
        return []
    lowered = text.lower()
    candidates = [
        str(asset.filename or ""),
        str((asset.metadata or {}).get("display_name") or ""),
        str((asset.metadata or {}).get("original_filename") or ""),
    ]
    exact_positions = _plain_positions_for_candidates(lowered, candidates)
    if exact_positions:
        return exact_positions
    filename = str(asset.filename or "")
    stem = filename.rsplit(".", 1)[0] if "." in filename else ""
    if len(stem.strip()) < 4 or stem.strip().lower() in {"logo", "product", "competitor", "image", "img", "photo"}:
        return []
    return _plain_positions_for_candidates(lowered, [stem])


def _plain_positions_for_candidates(text: str, candidates: list[str]) -> list[int]:
    positions: list[int] = []
    for candidate in {item.strip().lower() for item in candidates if item and item.strip()}:
        start = 0
        while True:
            index = text.find(candidate, start)
            if index < 0:
                break
            positions.append(index)
            start = index + len(candidate)
    return sorted(set(positions))


def _asset_role_from_text(text: str, asset: Any) -> str:
    positions = _asset_positions_in_text(text, asset)
    if not positions:
        return ""
    lowered = text.lower()
    role_terms = {
        "logo": ["logo", "brand logo", "标志", "商标", "品牌logo", "品牌 logo"],
        "vi_reference": ["vi", "brand guide", "brand guideline", "品牌规范", "视觉规范", "品牌参考"],
        "competitor_info": ["competitor", "rival", "竞品图", "竞品资料", "竞品", "竞对", "对手", "爆款"],
        "product_image": [
            "product image",
            "product photo",
            "product reference",
            "产品拼装图",
            "产品收藏系列",
            "产品系列",
            "产品参考图",
            "产品图",
            "产品图片",
            "参考图",
            "主图",
            "效果图",
            "拼装图",
            "系列图",
            "收藏系列",
            "产品外观",
        ],
    }
    for position in positions:
        before = lowered[max(0, position - 48): position]
        best: tuple[int, str] | None = None
        for role, terms in role_terms.items():
            for term in terms:
                found = before.rfind(term.lower())
                if found >= 0 and (best is None or found > best[0]):
                    best = (found, role)
        if best:
            return best[1]
        after = lowered[position: position + 48]
        for role, terms in role_terms.items():
            if any(term.lower() in after for term in terms):
                return role
    return ""


def _is_generated_image_asset(asset: Any) -> bool:
    metadata = asset.metadata or {}
    role = str(metadata.get("asset_role") or "").lower()
    memory = metadata.get("asset_memory") if isinstance(metadata.get("asset_memory"), dict) else {}
    memory_role = str(memory.get("role") or memory.get("asset_role") or "").lower()
    generated_roles = {
        "generated_visual_base",
        "composed_design_output",
        "generated_design_output",
    }
    return role in generated_roles or memory_role in generated_roles


def _is_image_asset(asset: Any) -> bool:
    mime = (asset.mime_type or "").lower()
    filename = str(asset.filename or "").lower()
    return mime.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"))


def _asset_image_role(asset: Any) -> str:
    metadata = asset.metadata or {}
    analysis = metadata.get("image_analysis") if isinstance(metadata.get("image_analysis"), dict) else {}
    return str(analysis.get("image_role") or analysis.get("role") or "").lower()


def _asset_memory_role(asset: Any) -> str:
    metadata = asset.metadata or {}
    memory = metadata.get("asset_memory") if isinstance(metadata.get("asset_memory"), dict) else {}
    return str(memory.get("role") or memory.get("asset_role") or "").lower()


def _is_product_reference_asset(asset: Any, *, text_role: str = "") -> bool:
    if not _is_image_asset(asset):
        return False
    if text_role in {"logo", "vi_reference", "competitor_info"}:
        return False
    metadata = asset.metadata or {}
    memory = metadata.get("asset_memory") if isinstance(metadata.get("asset_memory"), dict) else {}
    role = _asset_image_role(asset)
    memory_role = _asset_memory_role(asset)
    filename = str(asset.filename or "").lower()
    return any(
        [
            text_role == "product_image",
            asset.kind in {"transparent_product_image", "product_image"},
            _asset_has_role(asset, {"product_image"}),
            bool(metadata.get("preferred_product_reference")),
            role in {"product_image", "transparent_product_image", "primary_product_cutout", "product_reference_image"},
            memory_role in {"primary_product_cutout", "product_reference_image", "product_image"},
            bool(memory.get("candidate_reference")) and not _is_non_product_reference_asset(asset, text_role=text_role),
            any(term in filename for term in ["product", "产品", "产品图", "参考图", "效果图", "主图", "hero", "render"]),
        ]
    )


def _is_logo_reference_asset(asset: Any, *, text_role: str = "") -> bool:
    filename = str(asset.filename or "").lower()
    return text_role == "logo" or asset.kind == "logo" or _asset_has_role(asset, {"logo"}) or _asset_image_role(asset) == "logo" or "logo" in filename


def _is_vi_reference_asset(asset: Any, *, text_role: str = "") -> bool:
    role = _asset_image_role(asset)
    return text_role == "vi_reference" or asset.kind == "vi_document" or _asset_has_role(asset, {"vi_reference"}) or role in {"vi_reference", "vi_reference_image", "brand_vi"}


def _is_competitor_reference_asset(asset: Any, *, text_role: str = "") -> bool:
    role = _asset_image_role(asset)
    filename = str(asset.filename or "").lower()
    return (
        text_role == "competitor_info"
        or asset.kind in {"competitor_image", "competitor_packaging", "competitor_detail_page"}
        or _asset_has_role(asset, {"competitor_info"})
        or role.startswith("competitor")
        or any(term in filename for term in ["competitor", "竞品", "对手", "爆款"])
    )


def _is_non_product_reference_asset(asset: Any, *, text_role: str = "") -> bool:
    return (
        _is_logo_reference_asset(asset, text_role=text_role)
        or _is_vi_reference_asset(asset, text_role=text_role)
        or _is_competitor_reference_asset(asset, text_role=text_role)
    )


def _is_generic_product_reference_candidate(asset: Any, *, text_role: str = "") -> bool:
    return _is_image_asset(asset) and not _is_non_product_reference_asset(asset, text_role=text_role)


def _generic_product_reference_candidates(project: ProjectRecord) -> list[Any]:
    candidates = [
        asset
        for asset in project.assets
        if _is_generic_product_reference_candidate(asset, text_role=_project_text_role(project, asset))
    ]
    return sorted(candidates, key=lambda asset: _generic_reference_sort_key(project, asset))


def _generic_reference_sort_key(project: ProjectRecord, asset: Any) -> tuple[int, str]:
    text_role = _project_text_role(project, asset)
    if text_role == "product_image":
        priority = 0
    elif _asset_has_role(asset, {"product_image"}):
        priority = 1
    else:
        priority = 5
    filename = str(asset.filename or "")
    return priority, filename


def _project_text_role(project: ProjectRecord, asset: Any) -> str:
    text = "\n".join(
        item
        for item in [
            project.brief.raw_text or "",
            project.brief.core_product_definition or "",
        ]
        if item
    ).lower()
    if not text:
        return ""
    exact_candidates = [
        str(asset.filename or ""),
        str((asset.metadata or {}).get("display_name") or ""),
        str((asset.metadata or {}).get("original_filename") or ""),
    ]
    stem = str(asset.filename or "")
    stem_candidates = [stem.rsplit(".", 1)[0]] if "." in stem else []
    positions = _mention_positions_for_candidates(text, exact_candidates)
    if not positions:
        positions = _mention_positions_for_candidates(text, stem_candidates)
    if not positions:
        return ""
    role_terms = {
        "logo": ["logo", "标志", "商标"],
        "vi_reference": ["vi", "品牌规范", "视觉规范", "品牌参考"],
        "competitor_info": ["竞品", "对手", "爆款"],
        "product_image": ["产品图", "产品图片", "参考图", "主图", "效果图"],
        "product_intro": ["产品介绍", "产品资料", "产品ppt", "产品 pdf", "产品文档", "介绍"],
    }
    for position in sorted(positions):
        before = text[max(0, position - 40): position]
        best: tuple[int, str] | None = None
        for role, terms in role_terms.items():
            for term in terms:
                found = before.rfind(term)
                if found >= 0 and (best is None or found > best[0]):
                    best = (found, role)
        if best:
            return best[1]
    return ""


def _mention_positions_for_candidates(text: str, candidates: list[str]) -> list[int]:
    positions: list[int] = []
    for candidate in {item.strip().lower() for item in candidates if item and item.strip()}:
        marker = f"@{candidate}"
        start = 0
        while True:
            index = text.find(marker, start)
            if index < 0:
                break
            positions.append(index)
            start = index + len(marker)
    return sorted(set(positions))


def _asset_by_id(project: ProjectRecord, asset_id: str):
    return next(asset for asset in project.assets if asset.id == asset_id)


def _asset_index(project: ProjectRecord) -> list[dict[str, Any]]:
    return _asset_index_from_assets(project.assets)


def _asset_index_from_assets(assets: list[Any]) -> list[dict[str, Any]]:
    index: list[dict[str, Any]] = []
    for asset in assets:
        metadata = asset.metadata or {}
        index.append(
            {
                "id": asset.id,
                "kind": asset.kind,
                "filename": asset.filename,
                "mime_type": asset.mime_type,
                "roles": metadata.get("role_bindings", []),
                "processing": metadata.get("processing", {}),
                "has_document_parse": isinstance(metadata.get("document_parse"), dict),
                "has_image_analysis": isinstance(metadata.get("image_analysis"), dict),
            }
        )
    return index


def _asset_has_role(asset: Any, roles: set[str]) -> bool:
    bindings = asset.metadata.get("role_bindings") if isinstance(asset.metadata.get("role_bindings"), list) else []
    return any(
        isinstance(binding, dict) and binding.get("active", True) and binding.get("role") in roles
        for binding in bindings
    )


def _fallback_planner_decision(
    *,
    user_message: str,
    workflow_type: str,
    has_confirmed_usps: bool,
    has_confirmed_vi_profile: bool,
    has_confirmed_strategy: bool,
    pending_review_gate_type: str,
) -> PlannerDecision:
    text = user_message.lower()
    workflow_patch: dict[str, Any] = {}
    inferred_workflow = workflow_type
    if "详情" in user_message or "detail" in text:
        inferred_workflow = "detail_page"
        workflow_patch["workflow_type"] = "detail_page"
    elif "包装" in user_message or "packaging" in text:
        inferred_workflow = "packaging"
        workflow_patch["workflow_type"] = "packaging"

    if pending_review_gate_type:
        return PlannerDecision(
            intent="await_human_review",
            next_action="status",
            need_human_review=True,
            review_gate_type=pending_review_gate_type,
            message_to_user="当前有待确认卡片，请先确认、修改或退回。",
            state_patch=workflow_patch,
            reason="存在未处理的 ReviewGate。",
        )

    if "卖点" in user_message or "usp" in text or not has_confirmed_usps:
        return PlannerDecision(
            intent="extract_selling_points",
            next_action="call_agent",
            target_agent="usp_agent",
            required_tools=["memory_search"],
            need_human_review=True,
            review_gate_type="usp_review",
            message_to_user="我会先提炼核心卖点和次要卖点，完成后请你确认。",
            state_patch=workflow_patch,
            reason="需要先形成可确认的卖点上下文。",
        )

    if not has_confirmed_vi_profile:
        return PlannerDecision(
            intent="understand_brand_vi",
            next_action="call_agent",
            target_agent="vi_understanding_agent",
            required_tools=["memory_search", "image_understanding"],
            need_human_review=True,
            review_gate_type="vi_review",
            message_to_user="我会先理解品牌 VI、LOGO 和视觉约束，确认后再输出包装/详情策略。",
            state_patch=workflow_patch,
            reason="策略输出前必须先确认品牌视觉规范。",
        )

    target = "detail_page_strategy_agent" if inferred_workflow == "detail_page" else "packaging_strategy_agent"
    gate_type = "detail_strategy_review" if inferred_workflow == "detail_page" else "packaging_strategy_review"
    if has_confirmed_strategy:
        target = "detail_designer_agent" if inferred_workflow == "detail_page" else "packaging_designer_agent"
        gate_type = "final_design_review"
    return PlannerDecision(
        intent="generate_strategy",
        next_action="call_agent",
        target_agent=target,
        required_tools=["memory_search"],
        need_human_review=True,
        review_gate_type=gate_type,
        message_to_user="我会基于已确认卖点生成下一步策略卡片。",
        state_patch=workflow_patch,
        reason="卖点已确认，可以进入策略输出。",
    )


def _fallback_usps(*, project: ProjectRecord, source_message: str) -> USPCandidates:
    expectations = project.brief.user_expectations or project.brief.user_metrics or ["好玩", "安全", "视觉吸引"]
    product = project.brief.core_product_definition or project.brief.category or "产品"
    value = project.brief.value_proposition or source_message[:80] or "差异化价值"
    core_headline = f"围绕{product}做出差异化体验"[:18]
    core_angle = "待资料校准的核心方向"
    return USPCandidates(
        core=[
            USPItem(
                title=f"「{core_headline}」——{core_angle}",
                headline=core_headline,
                angle=core_angle,
                content=f"围绕“{value}”提炼第一核心卖点，后续需要用产品资料和竞品资料继续校准。",
                description=f"围绕“{value}”提炼第一核心卖点，后续需要用产品资料和竞品资料继续校准。",
                aligned_expectations=expectations[:3],
                user_alignment=USPUserAlignment(
                    parent="需要看到产品能带来的明确成长、品质或购买理由，当前仍需资料补强。",
                    child="需要看到玩法是否足够直观、有趣、容易产生完成感。",
                ),
                product_evidence=["来自用户初始项目描述，等待 PPT/图片解析补强证据"],
                product_visual_evidence="当前仅能基于项目描述判断，需等待产品图、PPT 或 PDF 解析后确认视觉证据。",
                competitor_comparison="竞品资料尚未完整解析，当前为方向性竞争力判断。",
                competitor_comparison_rows=[
                    USPCompetitorComparisonRow(
                        dimension="竞品证据",
                        competitor="当前竞品证据不足",
                        our_product="基于自家资料形成初步判断，需补充竞品资料校准",
                    )
                ],
                competitiveness_judgement="当前只能形成方向性判断，正式卖点需等待产品资料和竞品资料共同验证。",
                visual_usage=USPVisualUsage(
                    package_headline=core_headline,
                    short_tags=["待资料校准", "核心体验", "视觉证据待补强"],
                    visual_event="用产品核心玩法或造型形成一个可被看见的画面动作，待资料解析后细化。",
                    required_visual_elements=["产品主图", "核心玩法证据", "竞品对照依据"],
                    recommended_package_area="正面主视觉待定",
                ),
                confidence=0.55,
            )
        ],
        secondary=[
            USPItem(
                title="资料完整度和视觉呈现可作为辅助卖点",
                headline="资料完整度和视觉呈现",
                angle="辅助购买理由",
                content="配件、尺寸、玩法步骤和视觉吸引力可作为包装侧面或详情页后续屏的补充信息。",
                description="配件、尺寸、玩法步骤和视觉吸引力可作为包装侧面或详情页后续屏的补充信息。",
                aligned_expectations=expectations[:2],
                product_evidence=["等待资料解析确认"],
                product_visual_evidence="等待产品资料解析后确认可展示的配件、尺寸、玩法步骤或产品细节。",
                competitor_comparison="补齐竞品资料后再做强弱对比。",
                competitiveness_judgement="这是补充型卖点，竞争力强弱需要等资料解析后判断。",
                visual_usage=USPVisualUsage(
                    package_headline="资料完整，买前看得清",
                    short_tags=["配件", "尺寸", "玩法步骤"],
                    visual_event="把配件、尺寸或玩法步骤做成底部信息区，辅助用户快速理解。",
                    required_visual_elements=["配件清单", "尺寸或步骤信息"],
                    recommended_package_area="底部信息区或侧面展示区",
                ),
                confidence=0.5,
            )
        ],
        notes=["这是保底卖点结果；正式审核前建议等待资料解析完成后重新提炼。"],
    )


def _ensure_usp_minimum(candidates: USPCandidates, fallback: USPCandidates) -> USPCandidates:
    safe = candidates.model_copy(deep=True)
    if not safe.core:
        safe.core = fallback.core
        core_title = fallback.core[0].title if fallback.core else "保底核心卖点"
        safe.notes.append(f"模型未返回核心卖点，系统已启用「{core_title}」，避免空审核卡。")
    if not safe.secondary:
        safe.secondary = fallback.secondary
        secondary_title = fallback.secondary[0].title if fallback.secondary else "保底次要卖点"
        safe.notes.append(f"模型未返回次要卖点，系统已启用「{secondary_title}」，避免空审核卡。")
    safe.core = [_normalize_usp_item_v2(item) for item in safe.core[:3]]
    safe.secondary = [_normalize_usp_item_v2(item) for item in safe.secondary[:3]]
    safe.notes = list(dict.fromkeys(note for note in safe.notes if note))
    return safe


def _normalize_usp_item_v2(item: USPItem) -> USPItem:
    if not item.headline:
        item.headline = item.title.strip("「」").split("——", 1)[0].strip() if item.title else item.description[:18]
    if not item.angle and "——" in item.title:
        item.angle = item.title.split("——", 1)[1].strip()
    if not item.title and item.headline:
        item.title = f"「{item.headline}」——{item.angle or '核心卖点'}"
    if not item.content:
        item.content = item.description
    if not item.description:
        item.description = item.content
    if not item.product_visual_evidence and item.product_evidence:
        item.product_visual_evidence = "；".join(item.product_evidence[:2])
    if not item.competitor_comparison and item.competitor_comparison_rows:
        item.competitor_comparison = "；".join(
            f"{row.dimension}: 竞品={row.competitor} / 本品={row.our_product}"
            for row in item.competitor_comparison_rows[:3]
            if row.dimension or row.competitor or row.our_product
        )
    if not item.competitiveness_judgement and item.competitor_comparison:
        item.competitiveness_judgement = item.competitor_comparison
    if not item.visual_usage.package_headline:
        item.visual_usage.package_headline = item.headline or item.title
    return item
