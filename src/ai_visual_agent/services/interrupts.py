from typing import Any


def serialize_interrupts(raw: Any) -> list[dict[str, Any]]:
    """Convert LangGraph interrupt objects into JSON-safe dictionaries."""

    if not raw:
        return []

    items = raw if isinstance(raw, list) else [raw]
    serialized: list[dict[str, Any]] = []
    for item in items:
        value = getattr(item, "value", item)
        namespace = getattr(item, "ns", None)
        resumable = getattr(item, "resumable", True)
        serialized.append(
            {
                "value": value,
                "namespace": namespace,
                "resumable": resumable,
            }
        )
    return serialized
