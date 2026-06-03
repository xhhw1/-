from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai_visual_agent.services.golden_regression import (
    list_golden_fixtures,
    run_golden_fixture,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AI Visual Agent golden regression fixtures.")
    parser.add_argument(
        "fixtures",
        nargs="*",
        help="Fixture names to run. Defaults to all fixtures under fixtures/golden.",
    )
    parser.add_argument(
        "--json-output",
        help="Optional path to write machine-readable results.",
    )
    args = parser.parse_args()

    fixture_names = args.fixtures or [fixture.name for fixture in list_golden_fixtures()]
    results = [run_golden_fixture(name).model_dump(mode="json") for name in fixture_names]
    payload: dict[str, Any] = {
        "passed": all(result["passed"] for result in results),
        "fixture_count": len(results),
        "results": results,
    }

    if args.json_output:
        output_path = Path(args.json_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(_console_summary(payload), ensure_ascii=False, indent=2))
    return 0 if payload["passed"] else 1


def _console_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "passed": payload["passed"],
        "fixture_count": payload["fixture_count"],
        "fixtures": [
            {
                "name": result["fixture_name"],
                "workflow_type": result["workflow_type"],
                "status": result["status"],
                "passed": result["passed"],
                "check_count": len(result["checks"]),
                "failed_checks": [
                    {
                        "path": check["path"],
                        "message": check["message"],
                    }
                    for check in result["checks"]
                    if not check["passed"]
                ],
            }
            for result in payload["results"]
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
