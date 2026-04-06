#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path

from doshitan_core import summarize_records


def load_records(path: Path) -> list[dict]:
    records: list[dict] = []
    if not path.exists():
        return records

    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def render_text(summary: dict) -> str:
    lines = ["Doshitan metrics summary"]
    modes = summary.get("modes", [])
    if not modes:
        lines.append("No records found.")
        return "\n".join(lines)

    for mode in modes:
        prompt_stats = summary.get("prompts_by_mode", {}).get(mode, {})
        session_stats = summary.get("sessions_by_mode", {}).get(mode, {})
        rule_counts = summary.get("rule_counts_by_mode", {}).get(mode, {})
        lines.append("")
        lines.append(f"Mode: {mode}")
        lines.append(f"  Prompts: {prompt_stats.get('prompts', 0)}")
        lines.append(f"  Hostile prompts: {prompt_stats.get('hostile_prompts', 0)}")
        lines.append(f"  Interventions applied: {prompt_stats.get('interventions_applied', 0)}")
        lines.append(f"  Average hostility score: {summary.get('average_scores', {}).get(mode, 0.0)}")
        lines.append(f"  Sessions: {session_stats.get('sessions', 0)}")
        if rule_counts:
            top_rules = ", ".join(f"{rule}={count}" for rule, count in list(rule_counts.items())[:5])
            lines.append(f"  Top matched rules: {top_rules}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate Doshitan metadata logs.")
    parser.add_argument(
        "--path",
        default=".claude/doshitan/metrics.ndjson",
        help="Path to the NDJSON metrics file.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the summary as JSON.",
    )
    args = parser.parse_args()

    records = load_records(Path(args.path))
    summary = summarize_records(records)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
