import importlib.util

import pytest

from ai_visual_agent.domain import AssetRef, ProjectBrief, ProjectCreateRequest
from ai_visual_agent.services.project_store import SqlProjectStore


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("sqlalchemy") is None,
    reason="SQLAlchemy optional dependency is not installed",
)


def test_sql_project_store_persists_project_and_assets(tmp_path) -> None:
    store = SqlProjectStore(f"sqlite:///{tmp_path / 'store.db'}")
    store.setup()

    created = store.create(
        ProjectCreateRequest(
            workflow_type="packaging",
            brief=ProjectBrief(
                category="玩具",
                target_user="亲子家庭",
                user_expectations=["安全", "好玩"],
                value_proposition="互动体验",
                core_product_definition="互动玩具套装",
            ),
        )
    )
    asset = AssetRef(
        kind="product_ppt",
        filename="product.pptx",
        uri="C:/tmp/product.pptx",
        mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )

    updated = store.add_asset(created.id, asset)
    loaded = store.get(created.id)

    assert updated.assets[0].filename == "product.pptx"
    assert loaded.brief.category == "玩具"
    assert loaded.assets[0].id == asset.id
    assert store.list()[0].id == created.id

    updated_asset = store.update_asset_metadata(
        project_id=created.id,
        asset_id=asset.id,
        metadata_patch={"image_analysis": {"image_role": "product_image"}},
    )
    assert updated_asset.metadata["image_analysis"]["image_role"] == "product_image"


def test_sql_project_store_lists_legacy_blank_owner_for_admin(tmp_path) -> None:
    store = SqlProjectStore(f"sqlite:///{tmp_path / 'store.db'}")
    store.setup()

    legacy = store.create(
        ProjectCreateRequest(
            workflow_type="packaging",
            brief=ProjectBrief(category="legacy useful project"),
        )
    )
    other = store.create(
        ProjectCreateRequest(
            owner_id="someone-else@example.com",
            workflow_type="packaging",
            brief=ProjectBrief(category="other project"),
        )
    )
    with store.engine.begin() as conn:
        conn.execute(store._text("UPDATE projects SET owner_id = '' WHERE id = :id"), {"id": legacy.id})

    visible = store.list(owner_id="1173817292@qq.com")

    assert legacy.id in {project.id for project in visible}
    assert other.id not in {project.id for project in visible}
