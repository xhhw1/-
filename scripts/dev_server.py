from __future__ import annotations

import os
from pathlib import Path
import sys
import traceback


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))
sys.stdout = open(LOG_DIR / "uvicorn.out.log", "a", encoding="utf-8", buffering=1)
sys.stderr = open(LOG_DIR / "uvicorn.err.log", "a", encoding="utf-8", buffering=1)

try:
    import uvicorn

    uvicorn.run("ai_visual_agent.main:app", host="127.0.0.1", port=8000, log_level="info")
except Exception:
    traceback.print_exc()
    raise
