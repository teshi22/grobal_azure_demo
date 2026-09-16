"""Validate versioned travel evaluation JSONL files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "app" / "backend"))

from app.schemas.evaluation import EvaluationCaseCreate  # noqa: E402


def validate_file(path: Path) -> int:
    seen_ids: set[str] = set()
    count = 0
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            item = EvaluationCaseCreate.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
        if item.id in seen_ids:
            raise ValueError(
                f"{path}:{line_number}: duplicate case id {item.id}"
            )
        seen_ids.add(item.id)
        count += 1
    if not count:
        raise ValueError(f"{path}: no evaluation cases")
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=REPOSITORY_ROOT
        / "evaluation-data"
        / "travel-request-cases.jsonl",
    )
    args = parser.parse_args()
    count = validate_file(args.path.resolve())
    print(f"Validated {count} evaluation cases in {args.path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
