from __future__ import annotations

import logging

from ai_visual_agent.config import get_settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Importing the service module registers background handlers with the task queue.
    from ai_visual_agent.services import conversation_service  # noqa: F401
    from ai_visual_agent.services.task_queue import background_task_queue

    settings = get_settings()
    logging.info(
        "Starting AI Visual Agent worker: backend=%s queue=%s concurrency=%s",
        settings.task_queue_backend,
        settings.task_queue_redis_queue_name,
        settings.background_worker_concurrency,
    )
    background_task_queue.run_worker_forever()


if __name__ == "__main__":
    main()
