#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path

from doshitan_core import DEFAULT_CONFIG, Summary, read_jsonl, summarize_records

EMPTY_COUNTS: dict[str, int] = {}


class CliArgs(argparse.Namespace):
    path: str = ""
    json: bool = False


def render_text(summary: Summary) -> str:
    lines = ["Doshitan metrics summary"]
    modes = summary["modes"]
    if not modes:
        lines.append("No records found.")
        return "\n".join(lines)

    prompts_by_mode = summary["prompts_by_mode"]
    sessions_by_mode = summary["sessions_by_mode"]
    rule_counts_by_mode = summary["rule_counts_by_mode"]
    average_scores = summary["average_scores"]

    for mode in modes:
        prompt_stats = prompts_by_mode.get(mode, EMPTY_COUNTS)
        session_stats = sessions_by_mode.get(mode, EMPTY_COUNTS)
        rule_counts = rule_counts_by_mode.get(mode, EMPTY_COUNTS)
        lines.append("")
        lines.append(f"Mode: {mode}")
        lines.append(f"  Prompts: {prompt_stats.get('prompts', 0)}")
        lines.append(f"  Hostile prompts: {prompt_stats.get('hostile_prompts', 0)}")
        lines.append(
            f"  Interventions applied: {prompt_stats.get('interventions_applied', 0)}"
        )
        lines.append(f"  Average hostility score: {average_scores.get(mode, 0.0)}")
        lines.append(f"  Sessions: {session_stats.get('sessions', 0)}")
        if rule_counts:
            top_rules = ", ".join(
                f"{rule}={count}" for rule, count in list(rule_counts.items())[:5]
            )
            lines.append(f"  Top matched rules: {top_rules}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate Doshitan metadata logs.")
    default_path = str(Path(DEFAULT_CONFIG["log_dir"]) / "metrics.ndjson")
    _ = parser.add_argument(
        "--path",
        default=default_path,
        help="Path to the NDJSON metrics file.",
    )
    _ = parser.add_argument(
        "--json",
        action="store_true",
        help="Print the summary as JSON.",
    )
    args = CliArgs()
    _ = parser.parse_args(namespace=args)

    records = read_jsonl(Path(args.path))
    summary = summarize_records(records)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
