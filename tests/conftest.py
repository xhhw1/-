from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def _configure_isolated_runtime() -> None:
    """Keep pytest data out of the developer's local app database."""

    if os.getenv("AI_VISUAL_AGENT_ALLOW_REAL_DATA_TESTS"):
        return

    root = Path(tempfile.gettempdir()) / "ai_visual_agent_pytest_runtime"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)

    os.environ["APP_ENV"] = "test"
    os.environ["PROJECT_STORE_BACKEND"] = "sqlite"
    os.environ["GRAPH_CHECKPOINT_BACKEND"] = "memory"
    os.environ["LOCAL_DATABASE_URL"] = f"sqlite:///{root / 'vision_agent_test.db'}"
    os.environ["STORAGE_DIR"] = str(root / "data")
    os.environ["AUTH_ENABLED"] = "false"
    os.environ["LLM_BACKEND"] = "mock"
    os.environ["IMAGE_GENERATION_BACKEND"] = "mock"
    os.environ["MULTIMODAL_BACKEND"] = "mock"


_configure_isolated_runtime()
