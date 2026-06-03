from __future__ import annotations

from typing import Any

from ai_visual_agent.domain import ProjectRecord


PRODUCT_DOCUMENT_KINDS = {"product_ppt", "product_pdf"}
PRODUCT_IMAGE_KINDS = {"product_image", "transparent_product_image"}


def workflow_requirements(project: ProjectRecord) -> dict[str, Any]:
    if project.workflow_type != "packaging":
        return {
            "ready": True,
            "required_items": [],
            "missing": [],
            "notes": ["Detail-page workflow requirements are not enforced in this interaction slice."],
        }

    has_product_doc = any(asset.kind in PRODUCT_DOCUMENT_KINDS for asset in project.assets)
    has_product_image = any(asset.kind in PRODUCT_IMAGE_KINDS for asset in project.assets)
    required_items = [
        {
            "key": "product_document",
            "label": "产品 PPT/PDF 资料",
            "ready": has_product_doc,
            "accepted_kinds": sorted(PRODUCT_DOCUMENT_KINDS),
        },
        {
            "key": "product_image",
            "label": "产品图片",
            "ready": has_product_image,
            "accepted_kinds": sorted(PRODUCT_IMAGE_KINDS),
        },
    ]
    missing = [item["label"] for item in required_items if not item["ready"]]
    return {
        "ready": not missing,
        "required_items": required_items,
        "missing": missing,
        "notes": [
            "竞品图、竞品包装、VI 规范和 LOGO 可以继续补充，但不阻塞资料解析启动。",
        ],
    }
