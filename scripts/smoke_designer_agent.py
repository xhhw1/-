from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import AssetRef, ProjectBrief, ProjectCreateRequest
from ai_visual_agent.services.design_generation import (
    DesignGenerationJob,
    OpenAIImageGenerationProvider,
)
from ai_visual_agent.services.project_store import project_store


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one real designer-agent image generation smoke test.")
    parser.add_argument(
        "--image",
        default=r"C:\Users\admin\Desktop\cae73b7fffd12cca357da2357d962ee2.png",
        help="Primary local product reference image path.",
    )
    parser.add_argument(
        "--reference",
        action="append",
        default=[],
        help="Additional local reference image path. Can be passed multiple times for product/VI/logo references.",
    )
    args = parser.parse_args()

    reference_paths = [Path(args.image), *[Path(item) for item in args.reference]]
    missing_paths = [str(path) for path in reference_paths if not path.exists()]
    if missing_paths:
        raise SystemExit(f"missing image: {', '.join(missing_paths)}")

    settings = get_settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY missing")

    assets = [
        AssetRef(
            kind="product_image" if index == 0 else "vi_document",
            filename=image_path.name,
            uri=str(image_path.resolve()),
            mime_type=_mime_type_for(image_path),
            metadata={
                "storage": "local",
                "source": "smoke_designer_agent",
                "preferred_product_reference": index == 0,
            },
        )
        for index, image_path in enumerate(reference_paths)
    ]
    project = project_store.create(
        ProjectCreateRequest(
            workflow_type="packaging",
            brief=ProjectBrief(
                category="儿童玩具",
                target_user="1岁以上宝宝及亲子家庭",
                user_expectations=["安全", "好玩", "多场景"],
                value_proposition="安全、环保无毒、有趣好玩、适应场景多",
                core_product_definition="儿童转转乐哄娃神器",
            ),
            assets=assets,
        )
    )

    job = DesignGenerationJob(
        project_id=project.id,
        workflow_type="packaging",
        name="front",
        prompt=(
            "生成儿童转转乐玩具的电商包装正面海报主视觉。必须参考上传产品图，严格保持产品外观结构、"
            "四个圆形旋转玩具位、顶部透明珠子仓、黄色中部和底部吸盘。浅色婴童场景，产品居中大面积展示，"
            "柔和高端电商质感，预留中文标题和LOGO位置，不生成英文文字、水印、条码或虚构LOGO。"
        ),
        layout_spec={
            "surface": "front",
            "workflow_type": "packaging",
            "product_name": "儿童转转乐哄娃神器",
            "required_copy": ["轻轻一转，快乐不停", "吸住餐桌、浴缸，哪里都能玩"],
            "required_icons": ["1岁+", "安全提示", "玩法图标"],
            "tone": "明亮、柔和、婴童友好",
        },
        reference_asset_ids=[asset.id for asset in assets],
        vi_profile={
            "brand_colors": ["浅蓝", "奶油白", "柔和黄色"],
            "layout_rules": ["不虚构LOGO，仅预留LOGO区域", "中文文案后续叠加", "产品形态必须与参考图一致"],
            "forbidden_rules": ["不生成英文", "不改变产品结构", "不添加不存在的配件"],
        },
    )

    result = OpenAIImageGenerationProvider().generate_base(job)
    print(
        json.dumps(
            {
                "project_id": project.id,
                "source_asset_ids": [asset.id for asset in assets],
                "generated_asset_id": result.id,
                "uri": result.uri,
                "engine": result.metadata.get("engine"),
                "model": result.metadata.get("model"),
                "base_url": result.metadata.get("base_url"),
                "size": result.metadata.get("size"),
                "reference_asset_ids": result.metadata.get("reference_asset_ids"),
                "prompt_preview": str(result.metadata.get("prompt", ""))[:700],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _mime_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


if __name__ == "__main__":
    main()
