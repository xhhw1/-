from __future__ import annotations

from ai_visual_agent.domain import AssetRef
from ai_visual_agent.services.storage import S3BackedAssetStorage


class FakeS3Client:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.downloads: list[tuple[str, str, str]] = []

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self.downloads.append((bucket, key, filename))
        with open(filename, "wb") as handle:
            handle.write(self.content)


def test_s3_storage_restores_missing_local_cache(tmp_path) -> None:
    storage = S3BackedAssetStorage.__new__(S3BackedAssetStorage)
    storage.root = tmp_path / "assets"
    storage.bucket = "vision-agent"
    storage._client = FakeS3Client(b"asset-bytes")
    local_path = storage.root / "project-1" / "asset.png"
    asset = AssetRef(
        id="asset-1",
        kind="product_image",
        filename="asset.png",
        uri=str(local_path),
        mime_type="image/png",
        metadata={
            "storage": "s3",
            "local_cache_uri": str(local_path),
            "s3_bucket": "vision-agent",
            "s3_key": "project-1/asset.png",
        },
    )

    restored = storage.ensure_local_file(asset)

    assert restored == local_path.resolve()
    assert restored.read_bytes() == b"asset-bytes"
    assert storage._client.downloads == [("vision-agent", "project-1/asset.png", str(restored))]
