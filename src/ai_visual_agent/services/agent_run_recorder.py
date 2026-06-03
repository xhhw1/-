from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ai_visual_agent.domain import AgentRunRecord
from ai_visual_agent.services.audit_store import audit_store
from ai_visual_agent.services.structured_llm import StructuredLLMResult


def record_agent_run(
    *,
    project_id: str,
    stage: str,
    agent_name: str,
    result: StructuredLLMResult[BaseModel],
    input_context: dict[str, Any],
    model_role: str = "strategy",
) -> AgentRunRecord:
    output = result.output.model_dump(mode="json")
    record = AgentRunRecord(
        project_id=project_id,
        stage=stage,
        agent_name=agent_name,
        prompt_name=result.prompt_name,
        prompt_version=result.prompt_version,
        prompt_hash=result.prompt_hash,
        model_backend=result.backend,
        model_name=result.model,
        model_role=model_role,
        output_schema=result.output_schema,
        input_context=_compact(input_context),
        input_summary=_summarize_context(input_context),
        output=output,
        output_summary=_summarize_output(output),
        fallback_used=result.fallback_used,
        error=result.error,
    )
    audit_store.record(
        project_id=project_id,
        record_type="agent_run",
        stage=stage,
        payload=record.model_dump(mode="json"),
    )
    return record


def _compact(value: Any, max_string: int = 4000, max_items: int = 20) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_string else f"{value[:max_string].rstrip()}..."
    if isinstance(value, dict):
        return {
            str(key): _compact(item, max_string=max_string, max_items=max_items)
            for key, item in list(value.items())[:max_items]
        }
    if isinstance(value, list):
        return [_compact(item, max_string=max_string, max_items=max_items) for item in value[:max_items]]
    if isinstance(value, tuple):
        return [_compact(item, max_string=max_string, max_items=max_items) for item in value[:max_items]]
    return value


def _summarize_context(context: dict[str, Any]) -> str:
    keys = ", ".join(sorted(context.keys()))
    brief = context.get("project_brief") or {}
    category = brief.get("category") if isinstance(brief, dict) else ""
    product = brief.get("core_product_definition") if isinstance(brief, dict) else ""
    return " | ".join(part for part in [f"keys={keys}", str(category), str(product)] if part)


def _summarize_output(output: dict[str, Any]) -> str:
    if "core" in output:
        return f"core={len(output.get('core') or [])}, secondary={len(output.get('secondary') or [])}"
    if "screens" in output:
        return f"screens={len(output.get('screens') or [])}, theme={output.get('page_theme', '')}"
    if "front_layout" in output:
        return f"product={output.get('product_name', '')}, copy={len(output.get('required_copy') or [])}"
    if "layout_rules" in output:
        return f"colors={len(output.get('brand_colors') or [])}, rules={len(output.get('layout_rules') or [])}"
    return f"fields={len(output)}"
