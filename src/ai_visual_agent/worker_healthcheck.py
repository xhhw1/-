from __future__ import annotations

import sys

from ai_visual_agent.config import get_settings
from ai_visual_agent.services.task_queue import list_worker_heartbeats


def main() -> int:
    settings = get_settings()
    if str(settings.task_queue_backend).lower() != "redis":
        return 0
    try:
        heartbeats = list_worker_heartbeats(settings)
    except Exception as exc:
        print(f"worker healthcheck failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not heartbeats:
        print("worker healthcheck failed: no active worker heartbeat", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
