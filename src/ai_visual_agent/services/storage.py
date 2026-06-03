from io import BytesIO
import hashlib
from io import BytesIO
import re
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from ai_visual_agent.config import get_settings
from ai_visual_agent.domain import AssetKind, AssetRef


_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(filename: str) -> str:
    stem = Path(filename).stem or "asset"
    suffix = Path(filename).suffix.lower()
    safe_stem = _SAFE_FILENAME_RE.sub("_", stem).strip("._") or "asset"
    return f"{safe_stem[:80]}{suffix}"


def _physical_asset_kind(mime_type: str, filename: str) -> str:
    lowered = filename.lower()
    mime = (mime_type or "").lower()
    if mime.startswith("image/"):
        return "image"
    if lowered.endswith((".ppt", ".pptx")):
        return "ppt"
    if lowered.endswith(".pdf") or "pdf" in mime:
        return "pdf"
    if lowered.endswith((".doc", ".docx")):
        return "doc"
    if lowered.endswith((".xls", ".xlsx", ".csv")):
        return "excel"
    if mime.startswith("video/") or lowered.endswith((".mp4", ".mov", ".avi", ".webm")):
        return "video"
    return "other"


def _image_metadata(content: bytes, mime_or_name: str) -> dict:
    lowered = mime_or_name.lower()
    if not (lowered.startswith("image/") or Path(lowered).suffix in {".png", ".jpg", ".jpeg", ".webp"}):
        return {}
    try:
        from PIL import Image
    except Exception:
        return {}
    try:
        with Image.open(BytesIO(content)) as image:
            metadata = {"width": image.width, "height": image.height}
            if image.mode in {"RGBA", "LA"}:
                metadata["has_alpha"] = True
            return metadata
    except Exception:
        return {}


class LocalAssetStorage:
    """Local filesystem asset storage for MVP development."""

    def __init__(self, root: Path | None = None) -> None:
        settings = get_settings()
        self.root = root or settings.storage_dir / "assets"

    def project_dir(self, project_id: str) -> Path:
        return (self.root / project_id).resolve()

    async def save_upload(self, project_id: str, kind: AssetKind, upload: UploadFile) -> AssetRef:
        asset_id = str(uuid4())
        filename = _safe_filename(upload.filename or f"{asset_id}.bin")
        project_dir = self.root / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        path = project_dir / f"{asset_id}_{filename}"

        content = await upload.read()
        path.write_bytes(content)
        original_name = upload.filename or filename
        mime_type = upload.content_type or ""

        return AssetRef(
            id=asset_id,
            kind=kind,
            filename=original_name,
            uri=str(path.resolve()),
            mime_type=upload.content_type,
            metadata={
                "storage": "local",
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "asset_kind": _physical_asset_kind(mime_type, original_name),
                **_image_metadata(content, mime_type or original_name),
            },
        )

    def save_bytes(
        self,
        project_id: str,
        kind: AssetKind,
        filename: str,
        content: bytes,
        mime_type: str,
        metadata: dict | None = None,
    ) -> AssetRef:
        asset_id = str(uuid4())
        safe_name = _safe_filename(filename)
        project_dir = self.root / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        path = project_dir / f"{asset_id}_{safe_name}"
        path.write_bytes(content)

        merged_metadata = {"storage": "local", "size_bytes": len(content)}
        if metadata:
            merged_metadata.update(metadata)

        return AssetRef(
            id=asset_id,
            kind=kind,
            filename=filename,
            uri=str(path.resolve()),
            mime_type=mime_type,
            metadata=merged_metadata,
        )

    def delete_asset_file(self, asset: AssetRef) -> bool:
        path = self._safe_storage_path(asset.uri)
        if not path.exists():
            return False
        if path.is_file():
            path.unlink()
            return True
        return False

    def delete_project_assets(self, project_id: str) -> int:
        project_dir = self._safe_project_dir(project_id)
        if not project_dir.exists():
            return 0
        file_count = sum(1 for item in project_dir.rglob("*") if item.is_file())
        shutil.rmtree(project_dir)
        return file_count

    def list_orphan_project_dirs(self, active_project_ids: set[str]) -> list[dict[str, object]]:
        root = self.root.resolve()
        if not root.exists():
            return []
        orphans: list[dict[str, object]] = []
        for directory in root.iterdir():
            if not directory.is_dir():
                continue
            resolved = directory.resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            if directory.name in active_project_ids:
                continue
            files = [item for item in directory.rglob("*") if item.is_file()]
            orphans.append(
                {
                    "project_id": directory.name,
                    "path": str(resolved),
                    "file_count": len(files),
                    "size_bytes": sum(item.stat().st_size for item in files),
                }
            )
        return orphans

    def cleanup_orphan_project_dirs(self, active_project_ids: set[str]) -> dict[str, object]:
        orphans = self.list_orphan_project_dirs(active_project_ids)
        removed: list[dict[str, object]] = []
        for orphan in orphans:
            project_id = str(orphan["project_id"])
            removed_files = self.delete_project_assets(project_id)
            removed.append({**orphan, "removed_file_count": removed_files})
        return {
            "removed_count": len(removed),
            "removed_file_count": sum(int(item["removed_file_count"]) for item in removed),
            "removed_size_bytes": sum(int(item["size_bytes"]) for item in removed),
            "removed": removed,
        }

    def _safe_project_dir(self, project_id: str) -> Path:
        root = self.root.resolve()
        project_dir = (root / project_id).resolve()
        try:
            project_dir.relative_to(root)
        except ValueError as exc:
            raise ValueError("Project asset path is outside configured storage.") from exc
        return project_dir

    def _safe_storage_path(self, uri: str) -> Path:
        path = Path(uri)
        if not path.is_absolute():
            path = Path.cwd() / path
        resolved = path.resolve()
        root = self.root.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("Asset path is outside configured storage.") from exc
        return resolved


class S3BackedAssetStorage(LocalAssetStorage):
    """Keep a local tool-compatible cache while mirroring every asset to S3/MinIO."""

    def __init__(self, root: Path | None = None) -> None:
        super().__init__(root=root)
        settings = get_settings()
        self.bucket = settings.s3_bucket
        self._client = self._create_client()
        self._ensure_bucket()

    async def save_upload(self, project_id: str, kind: AssetKind, upload: UploadFile) -> AssetRef:
        asset = await super().save_upload(project_id=project_id, kind=kind, upload=upload)
        return self._mirror_asset(project_id, asset)

    def save_bytes(
        self,
        project_id: str,
        kind: AssetKind,
        filename: str,
        content: bytes,
        mime_type: str,
        metadata: dict | None = None,
    ) -> AssetRef:
        asset = super().save_bytes(project_id, kind, filename, content, mime_type, metadata)
        return self._mirror_asset(project_id, asset)

    def delete_asset_file(self, asset: AssetRef) -> bool:
        self._delete_object(asset)
        return super().delete_asset_file(asset)

    def delete_project_assets(self, project_id: str) -> int:
        self._delete_project_prefix(project_id)
        return super().delete_project_assets(project_id)

    def _mirror_asset(self, project_id: str, asset: AssetRef) -> AssetRef:
        path = self._safe_storage_path(asset.uri)
        key = f"{project_id}/{path.name}"
        extra_args = {}
        if asset.mime_type:
            extra_args["ContentType"] = asset.mime_type
        self._client.upload_file(
            str(path),
            self.bucket,
            key,
            ExtraArgs=extra_args or None,
        )
        asset.metadata.update(
            {
                "storage": "s3",
                "local_cache_uri": asset.uri,
                "s3_bucket": self.bucket,
                "s3_key": key,
                "s3_endpoint_url": get_settings().s3_endpoint_url,
            }
        )
        return asset

    def _delete_object(self, asset: AssetRef) -> None:
        key = asset.metadata.get("s3_key")
        bucket = str(asset.metadata.get("s3_bucket") or self.bucket)
        if not key:
            return
        try:
            self._client.delete_object(Bucket=bucket, Key=str(key))
        except Exception:
            return

    def _delete_project_prefix(self, project_id: str) -> None:
        prefix = f"{project_id}/"
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                if objects:
                    self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": objects})
        except Exception:
            return

    def _create_client(self):
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - optional dependency guard
            raise RuntimeError("boto3 is required when STORAGE_BACKEND=s3.") from exc
        settings = get_settings()
        return boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )

    def _ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except Exception:
            self._client.create_bucket(Bucket=self.bucket)


def create_asset_storage() -> LocalAssetStorage:
    backend = get_settings().storage_backend.lower().strip()
    if backend in {"s3", "minio"}:
        return S3BackedAssetStorage()
    return LocalAssetStorage()


asset_storage = create_asset_storage()
