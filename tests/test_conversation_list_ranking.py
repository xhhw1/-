from __future__ import annotations

from datetime import UTC, datetime

from ai_visual_agent.domain import (
    AssetRef,
    ConversationDetailResponse,
    ConversationReviewGate,
    ConversationSession,
    ProjectBrief,
    ProjectRecord,
)
from ai_visual_agent.services.conversation_service import _conversation_list_rank


def _detail(
    *,
    title: str,
    updated_at: datetime,
    output_uri: str = "",
    asset_id: str = "",
    include_asset: bool = False,
) -> ConversationDetailResponse:
    project = ProjectRecord(
        id=f"project-{title}",
        workflow_type="packaging",
        brief=ProjectBrief(raw_text=title),
        assets=[AssetRef(id=asset_id, kind="product_image", filename="front.png", uri=output_uri, mime_type="image/png")]
        if asset_id and include_asset
        else [],
    )
    session = ConversationSession(
        id=f"session-{title}",
        project_id=project.id,
        title=title,
        workflow_type="packaging",
        current_stage="final_design_review" if output_uri else "collecting_input",
        updated_at=updated_at,
    )
    gates = []
    if output_uri or asset_id:
        gates.append(
            ConversationReviewGate(
                session_id=session.id,
                type="final_design_review",
                title="请确认包装设计图",
                payload={
                    "generated_outputs": {
                        "items": [
                            {
                                "name": "front",
                                "asset_id": asset_id,
                                "uri": output_uri,
                            }
                        ]
                    }
                },
            )
        )
    return ConversationDetailResponse(session=session, project=project, review_gates=gates, assets=project.assets)


def test_conversation_list_rank_prioritizes_existing_generation_over_newer_placeholders(tmp_path) -> None:
    real_output = tmp_path / "packaging_front_base_r0.png"
    real_output.write_bytes(b"png")
    old_useful = _detail(
        title="拼装",
        updated_at=datetime(2026, 5, 29, tzinfo=UTC),
        output_uri=str(real_output),
    )
    newer_placeholder = _detail(
        title="Interactive Toy Set",
        updated_at=datetime(2026, 6, 3, tzinfo=UTC),
        output_uri="data/assets/asset-front.png",
        asset_id="asset-front",
        include_asset=False,
    )
    empty_new_project = _detail(
        title="baby spinning toy",
        updated_at=datetime(2026, 6, 3, 1, tzinfo=UTC),
    )

    ranked = sorted([newer_placeholder, empty_new_project, old_useful], key=_conversation_list_rank)

    assert ranked[0].session.title == "拼装"
    assert ranked[-1].session.title == "Interactive Toy Set"
