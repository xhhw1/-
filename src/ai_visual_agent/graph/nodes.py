from copy import deepcopy
from pathlib import Path
import re
from typing import Any

from langgraph.types import interrupt

from ai_visual_agent.domain import (
    AssetRef,
    CompetitorInsights,
    DetailPageStrategy,
    DetailScreenStrategy,
    PackagingStrategy,
    ProductMetadata,
    QCIssue,
    QCReport,
    USPCandidates,
    USPItem,
    VIProfile,
    MemoryUpsertRequest,
)
from ai_visual_agent.graph.state import GraphState
from ai_visual_agent.config import get_settings
from ai_visual_agent.services.agent_run_recorder import record_agent_run
from ai_visual_agent.services.audit_store import audit_store
from ai_visual_agent.services.design_generation import generate_design_outputs
from ai_visual_agent.services.image_analysis import analyze_image_asset, is_image_asset
from ai_visual_agent.services.memory_store import get_memory_store
from ai_visual_agent.services.project_store import project_store
from ai_visual_agent.services.structured_llm import StructuredLLMResult, invoke_structured
from ai_visual_agent.tools.document_tools import parse_document_file


def _brief(state: GraphState) -> dict[str, Any]:
    return state.get("project_brief", {})


def _feedback(state: GraphState, stage: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    feedback = list(state.get("human_feedback", []))
    feedback.append({"stage": stage, **result})
    return feedback


def _audit(state: GraphState, record_type: str, stage: str, payload: dict[str, Any]) -> None:
    project_id = state.get("project_id")
    if not project_id:
        return
    audit_store.record(
        project_id=str(project_id),
        record_type=record_type,  # type: ignore[arg-type]
        stage=stage,
        payload=payload,
    )


def _llm_metadata(result: StructuredLLMResult[Any]) -> dict[str, Any]:
    return result.metadata()


def _record_agent_run(
    state: GraphState,
    *,
    stage: str,
    agent_name: str,
    result: StructuredLLMResult[Any],
    input_context: dict[str, Any],
    model_role: str = "strategy",
) -> None:
    project_id = state.get("project_id")
    if not project_id:
        return
    record_agent_run(
        project_id=str(project_id),
        stage=stage,
        agent_name=agent_name,
        result=result,  # type: ignore[arg-type]
        input_context=input_context,
        model_role=model_role,
    )


def _asset_kind(asset: dict[str, Any]) -> str:
    return str(asset.get("kind") or "")


def _asset_metadata(asset: dict[str, Any]) -> dict[str, Any]:
    metadata = asset.get("metadata") or {}
    return metadata if isinstance(metadata, dict) else {}


def _image_analysis(asset: dict[str, Any]) -> dict[str, Any]:
    analysis = _asset_metadata(asset).get("image_analysis") or {}
    return analysis if isinstance(analysis, dict) else {}


def _image_understanding(asset: dict[str, Any]) -> dict[str, Any]:
    understanding = _image_analysis(asset).get("understanding") or {}
    return understanding if isinstance(understanding, dict) else {}


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def _visual_fact_lines(asset: dict[str, Any]) -> list[str]:
    analysis = _image_analysis(asset)
    understanding = _image_understanding(asset)
    fields = [
        understanding.get("summary") or analysis.get("semantic_summary"),
        understanding.get("product_appearance"),
        understanding.get("visible_accessories"),
        understanding.get("play_clues"),
        understanding.get("competitor_visual_hooks"),
        understanding.get("packaging_hierarchy"),
        understanding.get("detail_page_sections"),
    ]
    facts: list[str] = []
    for field in fields:
        facts.extend(_as_string_list(field))
    return facts


def _extract_keyword_lines(pages: list[dict[str, Any]], keywords: list[str], limit: int = 8) -> list[str]:
    matches: list[str] = []
    for page in pages:
        text = "\n".join([page.get("text", ""), page.get("ocr_text", "")])
        for line in text.splitlines():
            clean = " ".join(line.split())
            if clean and any(keyword.lower() in clean.lower() for keyword in keywords):
                matches.append(clean[:160])
            if len(matches) >= limit:
                return matches
    return matches


def _normalize_source_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("\\r", "\n").replace("\\n", "\n").replace("\\t", " ")
    text = re.sub(r"Text\(pages=\[", "", text)
    text = re.sub(r"TextPage\(page_number=\d+,\s*text=['\"]?", "", text)
    text = text.replace("'), ", "\n").replace('"), ', "\n")
    text = text.replace("')", "").replace('")', "")
    return text


def _clean_line(value: Any, max_chars: int = 180) -> str:
    text = " ".join(_normalize_source_text(value).split())
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _clean_line(value)
        if text:
            return text
    return ""


def _text_blob_from_state_parts(parsed_product: dict[str, Any], memory_notes: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for key in ("product_name", "dimensions", "accessories", "play_methods", "visual_features"):
        chunks.extend(_as_string_list(parsed_product.get(key)))
    for page in parsed_product.get("parsed_pages", []) or []:
        if not isinstance(page, dict):
            continue
        for key in ("title", "text", "ocr_text", "semantic_summary"):
            chunks.extend(_as_string_list(page.get(key)))
    for note in memory_notes:
        chunks.extend(_as_string_list(note.get("text")))
    flat: list[str] = []
    for chunk in chunks:
        if isinstance(chunk, list):
            flat.extend(chunk)
        else:
            flat.append(str(chunk))
    return _normalize_source_text("\n".join(line for line in flat if str(line).strip()))[:20000]


def _label_value(blob: str, labels: list[str]) -> str:
    blob = _normalize_source_text(blob)
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[:：]\s*([^\n\r]+)", blob)
        if match:
            return _clean_line(match.group(1))
        match = re.search(rf"{re.escape(label)}\s*[:：]\s*(?:\n|\r\n?)+\s*([^\n\r]+)", blob)
        if match:
            return _clean_line(match.group(1))
    return ""


def _contains_any(blob: str, keywords: list[str]) -> bool:
    lower = blob.lower()
    return any(keyword.lower() in lower for keyword in keywords)


def _evidence_lines(blob: str, keywords: list[str], limit: int = 3) -> list[str]:
    lines: list[str] = []
    for line in _normalize_source_text(blob).splitlines():
        clean = _clean_line(line, 220)
        if not clean:
            continue
        if any(keyword.lower() in clean.lower() for keyword in keywords):
            if clean not in lines:
                lines.append(clean)
        if len(lines) >= limit:
            return lines
    return lines


def _pick_expectations(expectations: list[str], keywords: list[str], fallback_count: int = 2) -> list[str]:
    picked = [
        item
        for item in expectations
        if any(keyword.lower() in item.lower() for keyword in keywords)
    ]
    return picked or expectations[:fallback_count]


def _competitor_sentence(competitor_insights: dict[str, Any], focus: str) -> str:
    competitors = competitor_insights.get("competitors") or []
    gaps = _as_string_list(competitor_insights.get("opportunity_gaps"))
    if competitors:
        return f"相较已上传竞品，优先用“{focus}”做可验证差异，并避开竞品同质化的视觉/文案表达。"
    if gaps:
        return f"当前竞品证据不足，先按机会点“{_clean_line(gaps[0], 80)}”强化“{focus}”，待补充竞品后再量化对比。"
    return f"当前未上传有效竞品素材，先将“{focus}”作为内部差异化方向，后续需用爆款竞品验证竞争力。"


def _build_fallback_usp_candidates(
    *,
    brief: dict[str, Any],
    parsed_product: dict[str, Any],
    competitor_insights: dict[str, Any],
    memory_notes: list[dict[str, Any]],
) -> USPCandidates:
    expectations = (
        _as_string_list(brief.get("user_expectations"))
        or _as_string_list(brief.get("user_metrics"))
        or ["好玩", "安全", "性价比高"]
    )
    blob = _text_blob_from_state_parts(parsed_product, memory_notes)
    product_name = _first_non_empty(
        brief.get("core_product_definition"),
        parsed_product.get("product_name"),
        _label_value(blob, ["品名", "产品品名", "Product Title"]),
        "核心产品",
    )
    value = _first_non_empty(brief.get("value_proposition"), "差异化产品体验")

    front_claim = _label_value(blob, ["正面卖点文案", "核心卖点", "Key Features"])
    back_claim = _label_value(blob, ["背面卖点文案"])
    core: list[USPItem] = []

    play_evidence = []
    if front_claim:
        play_evidence.append(f"正面卖点文案：{front_claim}")
    for line in _evidence_lines(
        blob,
        ["15+", "365+", "100+", "魔术", "magic", "玩法", "表演"],
        limit=4,
    ):
        if line not in play_evidence:
            play_evidence.append(line)
    play_evidence = play_evidence[:4]
    if front_claim or play_evidence:
        focus = front_claim or "多玩法魔术表演"
        core.append(
            USPItem(
                title=_clean_line(focus, 36),
                description=(
                    f"把“玩法数量多、可持续探索”作为第一核心卖点，用具体数字和魔术表演结果支撑"
                    f"{product_name}的第一眼吸引力。"
                ),
                aligned_expectations=_pick_expectations(expectations, ["好玩", "性价比", "玩法"], 2),
                product_evidence=play_evidence or [front_claim],
                competitor_comparison=_competitor_sentence(competitor_insights, "玩法数量和表演结果"),
                confidence=0.78,
            )
        )

    stage_evidence = []
    if back_claim:
        stage_evidence.append(f"背面卖点文案：{back_claim}")
    for line in _evidence_lines(
        blob,
        ["专属魔术舞台", "仪式感", "游轮", "cruise", "stage", "theatrical", "fantastical"],
        limit=4,
    ):
        if line not in stage_evidence:
            stage_evidence.append(line)
    stage_evidence = stage_evidence[:4]
    if stage_evidence:
        core.append(
            USPItem(
                title="专属魔术舞台，强化表演仪式感",
                description=(
                    f"将“{value}”落到可视化场景：让孩子不是只拿到道具，而是拥有一个可以展示、讲故事、表演的主题舞台。"
                ),
                aligned_expectations=_pick_expectations(expectations, ["好玩", "陪伴", "情感"], 2),
                product_evidence=stage_evidence,
                competitor_comparison=_competitor_sentence(competitor_insights, "主题舞台和仪式感"),
                confidence=0.76,
            )
        )

    easy_evidence = _evidence_lines(
        blob,
        ["零基础", "轻松上手", "8+", "ages 8", "step", "说明书", "educational", "STEAM"],
        limit=4,
    )
    if easy_evidence and len(core) < 3:
        core.append(
            USPItem(
                title="零基础轻松上手，亲子陪伴更容易发生",
                description=(
                    "把上手门槛和陪伴价值讲清楚，降低家长对复杂魔术道具的顾虑，同时让孩子获得可完成的表演成就感。"
                ),
                aligned_expectations=_pick_expectations(expectations, ["安全", "好玩", "陪伴"], 2),
                product_evidence=easy_evidence,
                competitor_comparison=_competitor_sentence(competitor_insights, "上手门槛和亲子陪伴"),
                confidence=0.72,
            )
        )

    if not core:
        core.append(
            USPItem(
                title=f"{product_name}的核心价值待确认",
                description=(
                    "当前资料证据不足，建议先补齐核心玩法、产品实拍/效果图、配件清单和竞品卖点，再进行正式卖点判断。"
                ),
                aligned_expectations=expectations[:3],
                product_evidence=["未从资料中提取到足够明确的卖点证据"],
                competitor_comparison=_competitor_sentence(competitor_insights, "核心玩法证据"),
                confidence=0.45,
            )
        )

    accessory_evidence = _evidence_lines(
        blob,
        ["配件", "道具", "魔棒", "扑克", "魔方", "硬币", "骰子", "帽", "accessor"],
        limit=4,
    )
    visual_evidence = _evidence_lines(
        blob,
        ["STEAM", "educational", "光影", "光能", "视觉", "glowing", "包装", "年龄", "battery"],
        limit=4,
    )
    secondary: list[USPItem] = []
    if accessory_evidence:
        secondary.append(
            USPItem(
                title="道具与配件丰富，支撑玩法可信度",
                description="在侧面或背面展示关键道具/配件，帮助用户相信“多玩法”不是空泛口号。",
                aligned_expectations=_pick_expectations(expectations, ["性价比", "好玩"], 2),
                product_evidence=accessory_evidence,
                competitor_comparison=_competitor_sentence(competitor_insights, "配件完整呈现"),
                confidence=0.7,
            )
        )
    if visual_evidence and len(secondary) < 3:
        secondary.append(
            USPItem(
                title="STEAM/光影视觉增强教育与吸引力",
                description="把教育属性和视觉效果作为辅助卖点，用于补充家长购买理由和孩子第一眼兴趣。",
                aligned_expectations=_pick_expectations(expectations, ["安全", "好玩", "教育"], 2),
                product_evidence=visual_evidence,
                competitor_comparison=_competitor_sentence(competitor_insights, "教育属性和视觉吸引"),
                confidence=0.68,
            )
        )
    if not secondary:
        secondary.append(
            USPItem(
                title="配件、尺寸与使用信息需要补强",
                description="后续包装/详情页需要补齐配件清单、尺寸、安全警示和使用场景，以提升转化信任。",
                aligned_expectations=expectations[:2],
                product_evidence=["当前结构化解析缺少配件或尺寸字段"],
                competitor_comparison="作为信任型补充卖点，需等竞品资料补齐后再比较。",
                confidence=0.55,
            )
        )

    missing_fields = _as_string_list(parsed_product.get("missing_fields"))
    notes = [
        f"模型不可用或返回异常时启用证据型 fallback；已读取 {len(memory_notes)} 条项目资料记忆。",
        "当前卖点优先引用 PPT、产品图理解和项目简报证据，避免没有证据的夸张表达。",
    ]
    if missing_fields:
        notes.append("结构化资料仍缺少：" + "、".join(missing_fields))
    if not (competitor_insights.get("competitors") or []):
        notes.append("暂未识别到有效竞品素材，竞争力对比为方向性判断。")

    return USPCandidates(core=core[:3], secondary=secondary[:3], notes=notes)


def _sanitize_usp_candidates(
    candidates: USPCandidates,
    *,
    brief: dict[str, Any],
    parsed_product: dict[str, Any],
    memory_notes: list[dict[str, Any]],
) -> USPCandidates:
    allowed_expectations = (
        _as_string_list(brief.get("user_expectations"))
        or _as_string_list(brief.get("user_metrics"))
    )
    evidence_blob = _text_blob_from_state_parts(parsed_product, memory_notes)
    sanitized = candidates.model_copy(deep=True)
    sanitized.core = [
        _sanitize_usp_item(item, allowed_expectations=allowed_expectations, evidence_blob=evidence_blob)
        for item in sanitized.core
    ]
    sanitized.secondary = [
        _sanitize_usp_item(item, allowed_expectations=allowed_expectations, evidence_blob=evidence_blob)
        for item in sanitized.secondary
    ]
    notes = list(sanitized.notes)
    if allowed_expectations:
        notes.append("系统已将 aligned_expectations 限定在用户输入的关注指标内。")
    if "认证" not in evidence_blob and any(
        "认证" in text
        for item in [*candidates.core, *candidates.secondary]
        for text in [item.title, item.description, *item.product_evidence]
    ):
        notes.append("系统已将无证据的资质类表述降级为教育属性/产品信息，避免违规卖点。")
    sanitized.notes = list(dict.fromkeys(_clean_line(note, 220) for note in notes if _clean_line(note)))
    return sanitized


def _sanitize_usp_item(
    item: USPItem,
    *,
    allowed_expectations: list[str],
    evidence_blob: str,
) -> USPItem:
    cleaned = item.model_copy(deep=True)
    cleaned.title = _sanitize_claim_text(cleaned.title, evidence_blob=evidence_blob, max_chars=48)
    cleaned.description = _sanitize_claim_text(
        cleaned.description,
        evidence_blob=evidence_blob,
        max_chars=260,
    )
    cleaned.product_evidence = _sanitize_evidence_lines(cleaned.product_evidence, evidence_blob=evidence_blob)
    if allowed_expectations:
        cleaned.aligned_expectations = _sanitize_expectations(
            cleaned.aligned_expectations,
            allowed_expectations=allowed_expectations,
            text=" ".join([cleaned.title, cleaned.description]),
        )
    cleaned.competitor_comparison = _clean_line(cleaned.competitor_comparison, 220)
    return cleaned


def _sanitize_claim_text(text: str, *, evidence_blob: str, max_chars: int) -> str:
    cleaned = _clean_line(text, max_chars)
    if "认证" in cleaned and "认证" not in evidence_blob and "certif" not in evidence_blob.lower():
        cleaned = cleaned.replace("STEAM认证", "STEAM教育属性")
        cleaned = cleaned.replace("steam认证", "STEAM教育属性")
        cleaned = cleaned.replace("安全认证", "安全信息")
        cleaned = cleaned.replace("认证", "属性")
    cleaned = cleaned.replace("???????", "待补充证据").replace("????", "待补充证据")
    return cleaned


def _sanitize_evidence_lines(lines: list[str], *, evidence_blob: str) -> list[str]:
    cleaned: list[str] = []
    for line in lines:
        text = _sanitize_claim_text(str(line), evidence_blob=evidence_blob, max_chars=220)
        if not text or "待补充证据" in text or "推测" in text or "可能含" in text:
            continue
        if text not in cleaned:
            cleaned.append(text)
    return cleaned or ["待补充明确资料证据"]


def _sanitize_expectations(
    values: list[str],
    *,
    allowed_expectations: list[str],
    text: str,
) -> list[str]:
    direct = [value for value in values if value in allowed_expectations]
    if direct:
        return direct[:3]

    mapped: list[str] = []
    text = text.lower()
    for expectation in allowed_expectations:
        if expectation in text:
            mapped.append(expectation)
    if not mapped:
        for expectation in allowed_expectations:
            if any(keyword in expectation for keyword in ["好玩", "玩法", "趣"]):
                mapped.append(expectation)
            elif any(keyword in expectation for keyword in ["性价比", "价值", "耐玩"]):
                mapped.append(expectation)
        if not mapped:
            mapped = allowed_expectations[:2]
    return list(dict.fromkeys(mapped))[:3]


def _ensure_image_analysis_for_assets(state: GraphState, assets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    settings = get_settings()
    if not settings.auto_analyze_images:
        return assets, []

    updated_assets = [deepcopy(asset) for asset in assets]
    errors: list[str] = []
    analyzed = 0
    for asset in updated_assets:
        if analyzed >= settings.auto_analyze_max_images:
            break
        metadata = _asset_metadata(asset)
        if isinstance(metadata.get("image_analysis"), dict):
            continue
        try:
            asset_ref = AssetRef.model_validate(asset)
            if not is_image_asset(asset_ref):
                continue
            analysis = analyze_image_asset(
                project_id=str(state.get("project_id") or ""),
                asset=asset_ref,
                workflow_type=state.get("workflow_type"),
                category=_brief(state).get("category"),
            )
            metadata["image_analysis"] = analysis.model_dump(mode="json")
            asset["metadata"] = metadata
            analyzed += 1
            try:
                project_store.update_asset_metadata(
                    project_id=str(state.get("project_id") or ""),
                    asset_id=asset_ref.id,
                    metadata_patch={"image_analysis": metadata["image_analysis"]},
                )
            except KeyError:
                pass
        except Exception as exc:
            errors.append(f"{asset.get('filename') or asset.get('id')}: {type(exc).__name__}: {exc}")
    return updated_assets, errors


def parse_inputs_node(state: GraphState) -> dict[str, Any]:
    brief = _brief(state)
    file_memory_context = state.get("file_memory_context", [])
    assets, image_analysis_errors = _ensure_image_analysis_for_assets(state, state.get("assets", []))
    product_images = [asset for asset in assets if asset.get("kind") == "product_image"]
    product_visual_features = [
        fact for asset in product_images for fact in _visual_fact_lines(asset)
    ]
    document_assets = [
        asset
        for asset in assets
        if _asset_kind(asset) in {"product_ppt", "product_pdf"}
        or str(asset.get("filename", "")).lower().endswith((".pptx", ".pdf"))
    ]

    parsed_pages: list[dict[str, Any]] = []
    document_errors: list[str] = []
    for asset in document_assets:
        try:
            parsed = parse_document_file(
                file_id=str(asset.get("id", "")),
                file_uri=str(asset.get("uri", "")),
                file_type=asset.get("mime_type") or asset.get("filename"),
            )
            for page in parsed.get("pages", []):
                page["source_asset_id"] = asset.get("id")
                page["source_filename"] = asset.get("filename")
                parsed_pages.append(page)
        except Exception as exc:
            document_errors.append(f"{asset.get('filename')}: {exc}")

    dimensions = _extract_keyword_lines(parsed_pages, ["尺寸", "size", "dimension", "cm", "mm"])
    accessories = _extract_keyword_lines(parsed_pages, ["配件", "accessory", "包含", "include"])
    play_methods = _extract_keyword_lines(parsed_pages, ["玩法", "play", "步骤", "how to", "功能"])

    metadata = ProductMetadata(
        category=brief.get("category", ""),
        product_name=brief.get("core_product_definition", "")[:40],
        dimensions=dimensions,
        accessories=accessories,
        play_methods=play_methods,
        hero_image_asset_id=product_images[0]["id"] if product_images else None,
        visual_features=product_visual_features
        or ["Run image analysis to extract product appearance, accessories, and play clues."],
        fact_sources={
            "brief": "user_input",
            "file_registry_memory": ",".join(
                str(item.get("memory_id") or "")
                for item in file_memory_context
                if isinstance(item, dict) and item.get("memory_id")
            ),
            "documents": ",".join(asset.get("filename", "") for asset in document_assets),
            "product_images": ",".join(asset.get("filename", "") for asset in product_images),
        },
        missing_fields=[
            field
            for field, values in {
                "dimensions": dimensions,
                "accessories": accessories,
                "play_methods": play_methods,
                "visual_features": product_visual_features,
            }.items()
            if not values
        ],
        parsed_pages=parsed_pages,
    )
    memory_store = get_memory_store()
    memory_ids: list[str] = []
    for page in parsed_pages:
        memory_text = "\n".join(
            part
            for part in [
                str(page.get("title") or ""),
                str(page.get("text") or ""),
                str(page.get("ocr_text") or ""),
                str(page.get("semantic_summary") or ""),
            ]
            if part
        ).strip()
        if not memory_text:
            continue
        memory_ids.append(
            memory_store.upsert(
                MemoryUpsertRequest(
                    text=memory_text,
                    memory_type="product_doc",
                    project_id=state.get("project_id"),
                    category=brief.get("category"),
                    workflow_type=state.get("workflow_type"),
                    asset_id=page.get("source_asset_id"),
                    source_type="document_page",
                    metadata={
                        "page_index": page.get("page_index"),
                        "source_filename": page.get("source_filename"),
                    },
                )
            )
        )
    for asset in product_images:
        memory_text = "\n".join(_visual_fact_lines(asset)).strip()
        if not memory_text:
            continue
        memory_ids.append(
            memory_store.upsert(
                MemoryUpsertRequest(
                    text=memory_text,
                    memory_type="product_doc",
                    project_id=state.get("project_id"),
                    category=brief.get("category"),
                    workflow_type=state.get("workflow_type"),
                    asset_id=asset.get("id"),
                    source_type="image_analysis_metadata",
                    metadata={
                        "image_role": _image_analysis(asset).get("image_role"),
                        "source_filename": asset.get("filename"),
                    },
                )
            )
        )

    updates: dict[str, Any] = {
        "parsed_product": metadata.model_dump(),
        "memory_context": [
            {
                "stage": "file_registry",
                "files": file_memory_context,
            },
            {"stage": "parse_inputs", "memory_ids": memory_ids},
        ],
        "status": "inputs_parsed",
    }
    parser_errors = [*document_errors, *image_analysis_errors]
    if parser_errors:
        updates["parser_errors"] = parser_errors
    _audit(
        state,
        "agent_output",
        "parse_inputs",
        {
            "parsed_product": metadata.model_dump(),
            "parser_errors": parser_errors,
            "file_memory_context": file_memory_context,
            "memory_ids": memory_ids,
        },
    )
    updates["assets"] = assets
    return updates


def analyze_competitors_node(state: GraphState) -> dict[str, Any]:
    competitor_assets = [
        asset for asset in state.get("assets", []) if str(asset.get("kind", "")).startswith("competitor")
    ]
    competitors: list[dict[str, Any]] = []
    analyzed_count = 0
    for asset in competitor_assets[:5]:
        analysis = _image_analysis(asset)
        understanding = _image_understanding(asset)
        if understanding:
            analyzed_count += 1
        ocr = analysis.get("ocr") if isinstance(analysis.get("ocr"), dict) else {}
        ocr_text = str(ocr.get("full_text") or "").strip()
        hooks = _as_string_list(understanding.get("competitor_visual_hooks"))
        summary = _as_string_list(understanding.get("summary") or analysis.get("semantic_summary"))
        competitors.append(
            {
                "asset_id": asset.get("id"),
                "selling_points": [ocr_text[:160]] if ocr_text else summary[:2],
                "visual_hooks": hooks or ["Run competitor image/video analysis to extract visual hooks."],
                "risks_to_avoid": _as_string_list(understanding.get("risks"))
                or ["Avoid directly copying competitor composition, claims, and copy."],
            }
        )
    insights = CompetitorInsights(
        summary=(
            f"{analyzed_count} competitor assets include image understanding metadata; "
            "use these hooks for differentiation, not copying."
        ),
        competitors=competitors,
        opportunity_gaps=[
            "Clarify the core play proof and make the product subject more readable than competitors."
        ],
        evidence=[
            " | ".join(
                item
                for item in [
                    str(asset.get("filename") or ""),
                    str(_image_understanding(asset).get("summary") or ""),
                ]
                if item
            )
            for asset in competitor_assets
        ],
    )
    _audit(state, "agent_output", "analyze_competitors", {"competitor_insights": insights.model_dump()})
    return {"competitor_insights": insights.model_dump(), "status": "competitors_analyzed"}


def generate_usps_node(state: GraphState) -> dict[str, Any]:
    brief = _brief(state)
    expectations = (
        _as_string_list(brief.get("user_expectations"))
        or _as_string_list(brief.get("user_metrics"))
        or ["好玩", "安全", "性价比高"]
    )
    parsed_product = state.get("parsed_product", {})
    if not isinstance(parsed_product, dict):
        parsed_product = {}
    competitor_insights = state.get("competitor_insights", {})
    if not isinstance(competitor_insights, dict):
        competitor_insights = {}
    value = _first_non_empty(brief.get("value_proposition"), "差异化产品体验")
    product_def = _first_non_empty(
        brief.get("core_product_definition"),
        parsed_product.get("product_name"),
        brief.get("category"),
        "核心产品",
    )
    memory_results = get_memory_store().search(
        query=" ".join([product_def, value, *expectations]),
        limit=5,
        project_id=state.get("project_id"),
        memory_type="product_doc",
    )
    memory_notes = [
        {
            "text": result.text[:240],
            "score": result.score,
            "payload": result.payload,
        }
        for result in memory_results
    ]

    candidates = _build_fallback_usp_candidates(
        brief=brief,
        parsed_product=parsed_product,
        competitor_insights=competitor_insights,
        memory_notes=memory_notes,
    )
    llm_context = {
        "project_brief": brief,
        "parsed_product": parsed_product,
        "competitor_insights": competitor_insights,
        "memory_results": memory_notes,
        "file_memory_context": state.get("file_memory_context", []),
        "parser_errors": state.get("parser_errors", []),
        "workflow_type": state.get("workflow_type"),
    }
    llm_result = invoke_structured(
        schema=USPCandidates,
        prompt_name="marketer",
        context=llm_context,
        fallback=candidates,
        model_role="strategy",
    )
    candidates = _sanitize_usp_candidates(
        llm_result.output,
        brief=brief,
        parsed_product=parsed_product,
        memory_notes=memory_notes,
    )
    llm_result = StructuredLLMResult(
        output=candidates,
        backend=llm_result.backend,
        model=llm_result.model,
        prompt_name=llm_result.prompt_name,
        prompt_version=llm_result.prompt_version,
        prompt_hash=llm_result.prompt_hash,
        output_schema=llm_result.output_schema,
        fallback_used=llm_result.fallback_used,
        error=llm_result.error,
    )
    _record_agent_run(
        state,
        stage="generate_usps",
        agent_name="Marketer Agent",
        result=llm_result,
        input_context=llm_context,
    )
    existing_context = list(state.get("memory_context", []))
    existing_context.append({"stage": "generate_usps", "results": memory_notes})
    _audit(
        state,
        "agent_output",
        "generate_usps",
        {
            "usp_candidates": candidates.model_dump(),
            "memory_results": memory_notes,
            "llm": _llm_metadata(llm_result),
        },
    )
    return {
        "usp_candidates": candidates.model_dump(),
        "memory_context": existing_context,
        "status": "usps_generated",
    }


def review_usps_node(state: GraphState) -> dict[str, Any]:
    result = interrupt(
        {
            "type": "usp_review",
            "title": "请审核核心卖点和次要卖点",
            "usp_candidates": state.get("usp_candidates", {}),
            "allowed_actions": ["approve", "edit", "reject"],
        }
    )
    action = result.get("action", "approve")
    selected = result.get("selected_usps") or state.get("usp_candidates", {})
    _audit(state, "human_review", "usp_review", result)
    return {
        "selected_usps": selected,
        "human_feedback": _feedback(state, "usp_review", result),
        "status": "usps_approved" if action != "reject" else "usps_rejected",
    }


def packaging_strategy_node(state: GraphState) -> dict[str, Any]:
    brief = _brief(state)
    selected = state.get("selected_usps", {})
    core_titles = [item.get("title", "") for item in selected.get("core", [])]
    product_name = brief.get("core_product_definition") or brief.get("category", "产品")
    main_copy = core_titles[0] if core_titles else "核心玩法一眼看懂"

    strategy = PackagingStrategy(
        product_name=product_name,
        box_type="天地盖/开窗盒二选一，MVP 阶段由人工审核确认",
        front_ratio="正面 1:1 或 4:5，侧面按盒型比例延展，顶面保留品牌识别区",
        side_ratio="按实际刀模尺寸配置",
        top_ratio="按实际刀模尺寸配置",
        overall_tone="高明度、强产品主体、清晰功能氛围，避免过度幻想化导致产品不一致",
        front_layout=(
            "产品主体占画面 55%-70%，背景服务于玩法理解；LOGO 放左上或右上，品名靠近主体；"
            f"主文案使用“{main_copy}”。"
        ),
        left_layout="展示玩法步骤或核心互动瞬间，配短句卖点和图标。",
        right_layout="展示配件、尺寸或第二玩法，强化购买确认。",
        back_layout="配件平铺、玩法说明、安规警告、厂商信息、条码区域分区排布。",
        required_copy=[main_copy],
        required_icons=["年龄标识", "安全提示", "玩法图标"],
        risk_notes=["文字和 LOGO 必须程序化叠加，不建议直接由生图模型生成。"],
    )
    llm_context = {
        "project_brief": brief,
        "parsed_product": state.get("parsed_product", {}),
        "selected_usps": selected,
        "competitor_insights": state.get("competitor_insights", {}),
        "human_feedback": state.get("human_feedback", []),
        "workflow_type": state.get("workflow_type"),
    }
    llm_result = invoke_structured(
        schema=PackagingStrategy,
        prompt_name="packaging_director",
        context=llm_context,
        fallback=strategy,
        model_role="strategy",
    )
    strategy = llm_result.output
    _record_agent_run(
        state,
        stage="packaging_strategy",
        agent_name="Packaging Director Agent",
        result=llm_result,
        input_context=llm_context,
    )
    _audit(
        state,
        "agent_output",
        "packaging_strategy",
        {"packaging_strategy": strategy.model_dump(), "llm": _llm_metadata(llm_result)},
    )
    return {"packaging_strategy": strategy.model_dump(), "status": "packaging_strategy_generated"}


def detail_strategy_node(state: GraphState) -> dict[str, Any]:
    brief = _brief(state)
    selected = state.get("selected_usps", {})
    core_titles = [item.get("title", "") for item in selected.get("core", [])]
    main_copy = core_titles[0] if core_titles else "核心卖点一屏讲清"

    strategy = DetailPageStrategy(
        page_theme=f"{brief.get('category', '产品')}详情页视觉提案",
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
        traffic_platform_notes="首屏兼顾电商主图点击，后续屏幕服务停留和转化。",
        risk_notes=["详情页文字建议程序化排版，避免生成图内文字不可控。"],
    )
    llm_context = {
        "project_brief": brief,
        "parsed_product": state.get("parsed_product", {}),
        "selected_usps": selected,
        "competitor_insights": state.get("competitor_insights", {}),
        "human_feedback": state.get("human_feedback", []),
        "workflow_type": state.get("workflow_type"),
    }
    llm_result = invoke_structured(
        schema=DetailPageStrategy,
        prompt_name="detail_page_director",
        context=llm_context,
        fallback=strategy,
        model_role="strategy",
    )
    strategy = llm_result.output
    _record_agent_run(
        state,
        stage="detail_strategy",
        agent_name="Detail Page Director Agent",
        result=llm_result,
        input_context=llm_context,
    )
    _audit(
        state,
        "agent_output",
        "detail_strategy",
        {"detail_page_strategy": strategy.model_dump(), "llm": _llm_metadata(llm_result)},
    )
    return {"detail_page_strategy": strategy.model_dump(), "status": "detail_strategy_generated"}


def review_strategy_node(state: GraphState) -> dict[str, Any]:
    workflow_type = state.get("workflow_type")
    strategy_key = "packaging_strategy" if workflow_type == "packaging" else "detail_page_strategy"
    result = interrupt(
        {
            "type": "strategy_review",
            "title": "请审核视觉策略",
            "workflow_type": workflow_type,
            "strategy": state.get(strategy_key),
            "allowed_actions": ["approve", "edit", "reject"],
        }
    )

    updates: dict[str, Any] = {
        "human_feedback": _feedback(state, "strategy_review", result),
        "status": "strategy_approved" if result.get("action") != "reject" else "strategy_rejected",
    }
    if workflow_type == "packaging" and result.get("packaging_strategy"):
        updates["packaging_strategy"] = result["packaging_strategy"]
    if workflow_type == "detail_page" and result.get("detail_page_strategy"):
        updates["detail_page_strategy"] = result["detail_page_strategy"]
    _audit(state, "human_review", "strategy_review", result)
    return updates


def parse_vi_node(state: GraphState) -> dict[str, Any]:
    vi_assets = [asset for asset in state.get("assets", []) if asset.get("kind") in {"vi_document", "logo"}]
    logo = next((asset for asset in vi_assets if asset.get("kind") == "logo"), None)
    vi_visual_rules = [fact for asset in vi_assets for fact in _visual_fact_lines(asset)]
    profile = VIProfile(
        brand_colors=["待从 VI 文档或 LOGO 中提取"],
        logo_asset_id=logo.get("id") if logo else None,
        typography_notes=" | ".join(vi_visual_rules[:3])
        or "Run image analysis on VI/logo assets to infer typography and layout hierarchy.",
        layout_rules=vi_visual_rules or ["LOGO, product name, and core copy are overlaid by layout code."],
        forbidden_rules=["不得改变产品结构、颜色、配件数量和核心外观"],
        source_asset_ids=[asset.get("id", "") for asset in vi_assets],
    )
    llm_context = {
        "vi_assets": vi_assets,
        "vi_visual_rules": vi_visual_rules,
        "parsed_product": state.get("parsed_product", {}),
        "packaging_strategy": state.get("packaging_strategy"),
        "detail_page_strategy": state.get("detail_page_strategy"),
    }
    llm_result = invoke_structured(
        schema=VIProfile,
        prompt_name="vi_guardian",
        context=llm_context,
        fallback=profile,
        model_role="fast",
    )
    profile = llm_result.output
    _record_agent_run(
        state,
        stage="parse_vi",
        agent_name="VI Guardian Agent",
        result=llm_result,
        input_context=llm_context,
        model_role="fast",
    )
    _audit(
        state,
        "agent_output",
        "parse_vi",
        {"vi_profile": profile.model_dump(), "llm": _llm_metadata(llm_result)},
    )
    return {"vi_profile": profile.model_dump(), "status": "vi_profile_generated"}


def generate_design_node(state: GraphState) -> dict[str, Any]:
    workflow_type = state.get("workflow_type")
    revision_round = int(state.get("revision_round", 0))
    strategy_key = "packaging_strategy" if workflow_type == "packaging" else "detail_page_strategy"
    reference_asset_ids = _product_reference_asset_ids(state)

    output = generate_design_outputs(
        project_id=str(state["project_id"]),
        workflow_type=str(workflow_type),
        strategy=state.get(strategy_key, {}),
        vi_profile=state.get("vi_profile", {}),
        revision_round=revision_round,
        reference_asset_ids=reference_asset_ids,
    )
    _audit(
        state,
        "agent_output",
        "generate_design",
        {"generated_outputs": output.model_dump(), "revision_round": revision_round},
    )
    return {"generated_outputs": output.model_dump(), "status": "design_generated"}


def _product_reference_asset_ids(state: GraphState) -> list[str]:
    assets = state.get("assets", [])
    priority_by_id = {
        str(item.get("asset_id")): 0 if item.get("preferred_product_reference") else 1
        for item in state.get("file_memory_context", [])
        if isinstance(item, dict) and item.get("candidate_reference") and item.get("asset_id")
    }
    candidates: list[tuple[int, str]] = []
    for asset in assets:
        asset_id = str(asset.get("id") or "")
        if not asset_id:
            continue
        kind = str(asset.get("kind") or "")
        metadata = _asset_metadata(asset)
        asset_memory = metadata.get("asset_memory") if isinstance(metadata.get("asset_memory"), dict) else {}
        if not (
            kind in {"transparent_product_image", "product_image"}
            or asset_memory.get("candidate_reference")
            or metadata.get("preferred_product_reference")
        ):
            continue
        if metadata.get("preferred_product_reference"):
            priority = 0
        elif kind == "transparent_product_image":
            priority = 1
        elif asset_id in priority_by_id:
            priority = 2 + priority_by_id[asset_id]
        else:
            priority = 4
        candidates.append((priority, asset_id))
    return [asset_id for _, asset_id in sorted(candidates)]


def quality_check_node(state: GraphState) -> dict[str, Any]:
    outputs = state.get("generated_outputs", {}).get("items", [])
    issues: list[QCIssue] = []
    if not outputs:
        issues.append(
            QCIssue(
                severity="blocking",
                category="asset",
                message="没有生成任何设计图资产。",
                suggested_fix="检查 Designer Agent 输出和资产存储配置。",
            )
        )
    else:
        issues.extend(_qc_expected_outputs(state, outputs))
        issues.extend(_qc_output_integrity(outputs))
        issues.extend(_qc_packaging_copy_alignment(state, outputs))

    blocking_or_high = any(issue.severity in {"blocking", "high"} for issue in issues)

    report = QCReport(
        passed=not blocking_or_high,
        score=_qc_score(issues),
        issues=issues,
        summary=_qc_summary(issues),
    )
    _audit(state, "qc_report", "quality_check", {"qc_report": report.model_dump()})
    return {"qc_report": report.model_dump(), "status": "qc_passed" if report.passed else "qc_failed"}


def _qc_expected_outputs(state: GraphState, outputs: list[dict[str, Any]]) -> list[QCIssue]:
    workflow_type = state.get("workflow_type")
    names = {str(item.get("name") or "") for item in outputs}
    if workflow_type == "packaging":
        expected = {"front", "left", "right", "back"}
    else:
        screens = state.get("detail_page_strategy", {}).get("screens", [])
        expected = {f"screen_{screen.get('screen_index')}" for screen in screens if screen.get("screen_index")}
        if not expected:
            expected = {f"screen_{index}" for index in range(1, 6)}

    missing = sorted(expected - names)
    if not missing:
        return []
    return [
        QCIssue(
            severity="blocking",
            category="asset",
            message=f"Missing required output surfaces: {', '.join(missing)}.",
            suggested_fix="Regenerate the missing packaging faces or detail screens.",
        )
    ]


def _qc_output_integrity(outputs: list[dict[str, Any]]) -> list[QCIssue]:
    issues: list[QCIssue] = []
    seen: set[str] = set()
    for item in outputs:
        name = str(item.get("name") or "")
        prompt = str(item.get("prompt") or "").strip()
        uri = str(item.get("uri") or "").strip()
        label = name or "<unnamed>"
        if not name:
            issues.append(
                QCIssue(
                    severity="high",
                    category="asset",
                    message="A generated output is missing its name.",
                    suggested_fix="Ensure every generated item has a stable face or screen name.",
                )
            )
        elif name in seen:
            issues.append(
                QCIssue(
                    severity="medium",
                    category="asset",
                    message=f"Duplicate generated output name: {name}.",
                    suggested_fix="Keep output names unique so reviewers can compare versions reliably.",
                )
            )
        seen.add(name)

        if not prompt:
            issues.append(
                QCIssue(
                    severity="high",
                    category="copy",
                    message=f"Output {label} has an empty generation prompt.",
                    suggested_fix="Regenerate from a complete strategy layout description.",
                )
            )
        if not uri:
            issues.append(
                QCIssue(
                    severity="blocking",
                    category="asset",
                    message=f"Output {label} has no asset URI.",
                    suggested_fix="Check asset storage during design generation.",
                )
            )
            continue

        path = Path(uri)
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists() or not path.is_file():
            issues.append(
                QCIssue(
                    severity="blocking",
                    category="asset",
                    message=f"Output {label} asset file does not exist.",
                    suggested_fix="Regenerate the missing asset or repair the storage URI.",
                )
            )
    return issues


def _qc_packaging_copy_alignment(state: GraphState, outputs: list[dict[str, Any]]) -> list[QCIssue]:
    if state.get("workflow_type") != "packaging":
        return []
    required_copy = _as_string_list(state.get("packaging_strategy", {}).get("required_copy"))
    if not required_copy:
        return []

    front = next((item for item in outputs if item.get("name") == "front"), {})
    front_prompt = str(front.get("prompt") or "")
    missing_copy = [copy for copy in required_copy if copy and copy not in front_prompt]
    if not missing_copy:
        return []
    return [
        QCIssue(
            severity="medium",
            category="copy",
            message=f"Front prompt does not include required copy: {', '.join(missing_copy)}.",
            suggested_fix="Add required selling-point copy to the front layout or programmatic overlay layer.",
        )
    ]


def _qc_score(issues: list[QCIssue]) -> float:
    if not issues:
        return 0.95
    penalty = 0.0
    weights = {"blocking": 0.45, "high": 0.25, "medium": 0.12, "low": 0.05}
    for issue in issues:
        penalty += weights.get(issue.severity, 0.1)
    return max(0.1, round(0.95 - penalty, 2))


def _qc_summary(issues: list[QCIssue]) -> str:
    if not issues:
        return (
            "Rule-based QC passed. Next production step: add OCR, VI color, "
            "product consistency, and multimodal scoring."
        )
    blocking = sum(1 for issue in issues if issue.severity == "blocking")
    high = sum(1 for issue in issues if issue.severity == "high")
    medium = sum(1 for issue in issues if issue.severity == "medium")
    low = sum(1 for issue in issues if issue.severity == "low")
    return (
        f"Rule-based QC found {len(issues)} issue(s): "
        f"blocking={blocking}, high={high}, medium={medium}, low={low}."
    )


def increment_revision_node(state: GraphState) -> dict[str, Any]:
    return {
        "revision_round": int(state.get("revision_round", 0)) + 1,
        "status": "revision_requested",
    }


def review_final_node(state: GraphState) -> dict[str, Any]:
    result = interrupt(
        {
            "type": "final_design_review",
            "title": "请审核最终设计图",
            "generated_outputs": state.get("generated_outputs", {}),
            "qc_report": state.get("qc_report", {}),
            "allowed_actions": ["approve", "edit", "reject"],
        }
    )
    status = "final_approved" if result.get("action") == "approve" else "final_revision_requested"
    _audit(state, "human_review", "final_review", result)
    return {"human_feedback": _feedback(state, "final_review", result), "status": status}


def archive_node(state: GraphState) -> dict[str, Any]:
    archive = {
        "project_id": state.get("project_id"),
        "workflow_type": state.get("workflow_type"),
        "outputs": deepcopy(state.get("generated_outputs", {})),
        "qc_report": deepcopy(state.get("qc_report", {})),
        "revision_round": state.get("revision_round", 0),
    }
    _audit(state, "archive_record", "archive", archive)
    return {"archive": archive, "status": "completed"}
