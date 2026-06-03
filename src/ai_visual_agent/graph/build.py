from langgraph.graph import END, START, StateGraph

try:
    from langgraph.checkpoint.memory import InMemorySaver as MemorySaver
except ImportError:  # pragma: no cover - compatibility with older LangGraph versions
    from langgraph.checkpoint.memory import MemorySaver

from ai_visual_agent.graph.nodes import (
    analyze_competitors_node,
    archive_node,
    detail_strategy_node,
    generate_design_node,
    generate_usps_node,
    increment_revision_node,
    packaging_strategy_node,
    parse_inputs_node,
    parse_vi_node,
    quality_check_node,
    review_final_node,
    review_strategy_node,
    review_usps_node,
)
from ai_visual_agent.graph.routing import (
    route_after_final_review,
    route_after_quality_check,
    route_after_strategy_review,
    route_after_usp_review,
)
from ai_visual_agent.graph.state import GraphState


def build_graph(checkpointer=None):
    builder = StateGraph(GraphState)

    builder.add_node("parse_inputs", parse_inputs_node)
    builder.add_node("analyze_competitors", analyze_competitors_node)
    builder.add_node("generate_usps", generate_usps_node)
    builder.add_node("review_usps", review_usps_node)
    builder.add_node("packaging_strategy", packaging_strategy_node)
    builder.add_node("detail_strategy", detail_strategy_node)
    builder.add_node("review_strategy", review_strategy_node)
    builder.add_node("parse_vi", parse_vi_node)
    builder.add_node("generate_design", generate_design_node)
    builder.add_node("quality_check", quality_check_node)
    builder.add_node("increment_revision", increment_revision_node)
    builder.add_node("review_final", review_final_node)
    builder.add_node("archive", archive_node)

    builder.add_edge(START, "parse_inputs")
    builder.add_edge("parse_inputs", "analyze_competitors")
    builder.add_edge("analyze_competitors", "generate_usps")
    builder.add_edge("generate_usps", "review_usps")
    builder.add_conditional_edges(
        "review_usps",
        route_after_usp_review,
        {
            "regenerate": "generate_usps",
            "packaging": "packaging_strategy",
            "detail_page": "detail_strategy",
        },
    )
    builder.add_edge("packaging_strategy", "review_strategy")
    builder.add_edge("detail_strategy", "review_strategy")
    builder.add_conditional_edges(
        "review_strategy",
        route_after_strategy_review,
        {
            "continue": "parse_vi",
            "packaging": "packaging_strategy",
            "detail_page": "detail_strategy",
        },
    )
    builder.add_edge("parse_vi", "generate_design")
    builder.add_edge("generate_design", "quality_check")
    builder.add_conditional_edges(
        "quality_check",
        route_after_quality_check,
        {"pass": "review_final", "revise": "increment_revision", "needs_human": "review_final"},
    )
    builder.add_edge("increment_revision", "generate_design")
    builder.add_conditional_edges(
        "review_final",
        route_after_final_review,
        {"archive": "archive", "revise": "increment_revision"},
    )
    builder.add_edge("archive", END)

    return builder.compile(checkpointer=checkpointer or MemorySaver())
