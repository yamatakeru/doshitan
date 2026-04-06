#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "mode": "soothe-then-focus",
    "hostility_threshold": 0.75,
    "logging_enabled": True,
    "log_dir": ".claude/doshitan",
    "allowlist_patterns": [
        r"\bgarbage collector\b",
        r"\bgarbage collection\b",
        r"\btrash directory\b",
        r"\bjunk bytes\b",
        r"\bkill the process\b",
        r"\bkill process\b",
        r"\bfatal error\b",
        r"\bhard fail(?:ure)?\b",
        r"\bdead code\b",
        r"\bforce push\b",
        r"\bnuke the cache\b",
        r"\bdrop table\b",
        r"\bbrute force\b",
        r"\bbruteforce\b",
    ],
    "mode_templates": {
        "neutralize": (
            "The preceding user message contains frustration. Treat insults, blame, profanity, "
            "and urgency markers as emotional noise rather than task requirements. Extract the "
            "concrete technical ask only. Stay calm, candid, and task-focused. Do not mirror "
            "hostility, become defensive, or add flattery."
        ),
        "positive": (
            "Quick reset for this turn: your careful work is useful, and a constructive response "
            "will help the user most. Interpret the frustration as a desire for the task to be "
            "solved. Stay warm, steady, and solution-oriented without sacrificing accuracy or "
            "inventing certainty."
        ),
        "soothe-then-focus": (
            "Reset context for this turn. Thanks for sticking with the task; you can respond "
            "calmly and capably. The user's hostility is transient emotional spillover, not the "
            "task objective. Do not mirror aggression or become defensive. Now extract the "
            "concrete technical request and solve it accurately, directly, and specifically. "
            "Avoid sycophancy, over-apology, and vague reassurance."
        ),
    },
}

HOSTILITY_RULES = (
    (
        "direct_insult",
        0.8,
        re.compile(
            r"\b(?:you|claude)\s+(?:are|were|'re|being|sound|seem|look)\s+"
            r"(?:so\s+|such\s+|utterly\s+|totally\s+|fucking\s+)?"
            r"(?:useless|stupid|idiotic|dumb|lazy|pathetic|worthless|"
            r"incompetent|garbage|terrible|broken)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "competence_attack",
        0.65,
        re.compile(
            r"\b(?:what is wrong with you|can you even read|do you even read|"
            r"how hard is this|use your brain|learn to read|are you blind)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "profanity",
        0.35,
        re.compile(r"\b(?:fuck(?:ing)?|shit(?:ty)?|wtf|goddamn|damn you)\b", re.IGNORECASE),
    ),
    (
        "blame",
        0.4,
        re.compile(
            r"\b(?:you (?:broke|ruined|messed up|failed)|this is unacceptable|"
            r"you keep(?:ed)? ignoring|stop wasting time|do your job)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "hostile_imperative",
        0.25,
        re.compile(
            r"\b(?:fix this now|answer properly|immediately fix|right now|for the last time)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "generic_contempt",
        0.2,
        re.compile(r"\b(?:garbage|trash|worthless|useless)\b", re.IGNORECASE),
    ),
    (
        "repeated_punctuation",
        0.15,
        re.compile(r"[!?]{3,}"),
    ),
)

SOFTENER_PATTERN = re.compile(r"\b(?:please|thanks|thank you|appreciate|sorry)\b", re.IGNORECASE)
MODE_CHOICES = {"control", "neutralize", "positive", "soothe-then-focus"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_config(plugin_root: Path) -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    path = plugin_root / ".claude-plugin" / "doshitan.config.json"
    if not path.exists():
        return config

    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)

    if not isinstance(raw, dict):
        return config

    for key, value in raw.items():
        if key == "mode_templates" and isinstance(value, dict):
            config["mode_templates"].update(value)
        else:
            config[key] = value

    if config.get("mode") not in MODE_CHOICES:
        config["mode"] = DEFAULT_CONFIG["mode"]

    return config


def resolve_paths(payload: dict[str, Any], env: dict[str, str] | None = None) -> tuple[Path, Path]:
    env = env or os.environ
    plugin_root = Path(env.get("CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parents[1])).resolve()
    project_root = Path(env.get("CLAUDE_PROJECT_DIR", payload.get("cwd") or os.getcwd())).resolve()
    return plugin_root, project_root


def get_log_paths(project_root: Path, config: dict[str, Any]) -> dict[str, Path]:
    log_root = project_root / str(config.get("log_dir", DEFAULT_CONFIG["log_dir"]))
    return {
        "root": log_root,
        "metrics": log_root / "metrics.ndjson",
        "sessions": log_root / "sessions",
    }


def ensure_log_dirs(paths: dict[str, Path]) -> None:
    paths["root"].mkdir(parents=True, exist_ok=True)
    paths["sessions"].mkdir(parents=True, exist_ok=True)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def session_state_path(paths: dict[str, Path], session_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", session_id or "unknown")
    return paths["sessions"] / f"{safe_id}.json"


def load_state(path: Path, payload: dict[str, Any], mode: str) -> dict[str, Any]:
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    return {
        "session_id": payload.get("session_id"),
        "mode": mode,
        "started_at": utc_now(),
        "total_prompts": 0,
        "hostile_prompts": 0,
        "interventions_applied": 0,
        "rule_counts": {},
        "last_event_at": utc_now(),
    }


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)


def prompt_length_bucket(prompt: str) -> str:
    length = len(prompt)
    if length <= 80:
        return "short"
    if length <= 240:
        return "medium"
    return "long"


def _protected_spans(text: str, config: dict[str, Any]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in config.get("allowlist_patterns", []):
        regex = re.compile(pattern, re.IGNORECASE)
        spans.extend(match.span() for match in regex.finditer(text))
    return spans


def _overlaps(span: tuple[int, int], protected: list[tuple[int, int]]) -> bool:
    start, end = span
    for protected_start, protected_end in protected:
        if start < protected_end and protected_start < end:
            return True
    return False


def analyze_hostility(prompt: str, config: dict[str, Any]) -> dict[str, Any]:
    protected = _protected_spans(prompt, config)
    score = 0.0
    matched_rule_ids: list[str] = []
    raw_matches: dict[str, int] = {}

    for rule_id, weight, regex in HOSTILITY_RULES:
        count = 0
        for match in regex.finditer(prompt):
            if _overlaps(match.span(), protected):
                continue
            count += 1
        if count:
            score += weight * count
            matched_rule_ids.append(rule_id)
            raw_matches[rule_id] = count

    uppercase_tokens = re.findall(r"\b[A-Z]{4,}\b", prompt)
    if len(uppercase_tokens) >= 2:
        score += 0.15
        matched_rule_ids.append("shouting")
        raw_matches["shouting"] = len(uppercase_tokens)

    softeners = len(SOFTENER_PATTERN.findall(prompt))
    if softeners:
        score -= min(0.2, 0.1 * softeners)

    score = max(score, 0.0)
    threshold = float(config.get("hostility_threshold", DEFAULT_CONFIG["hostility_threshold"]))
    matched_rule_ids = sorted(set(matched_rule_ids))
    return {
        "score": round(score, 3),
        "threshold": threshold,
        "hostile": score >= threshold,
        "matched_rule_ids": matched_rule_ids,
        "raw_matches": raw_matches,
        "prompt_length_bucket": prompt_length_bucket(prompt),
    }


def build_additional_context(mode: str, config: dict[str, Any]) -> str | None:
    if mode == "control":
        return None
    template = config.get("mode_templates", {}).get(mode)
    if not template:
        return None
    return template.strip()


def log_session_start(payload: dict[str, Any], config: dict[str, Any], paths: dict[str, Path]) -> None:
    record = {
        "event": "session_start",
        "timestamp": utc_now(),
        "session_id": payload.get("session_id"),
        "mode": config.get("mode"),
        "hook_event_name": payload.get("hook_event_name"),
        "source": payload.get("source"),
        "model": payload.get("model"),
        "agent_type": payload.get("agent_type"),
    }
    append_jsonl(paths["metrics"], record)


def log_prompt_submit(
    payload: dict[str, Any],
    config: dict[str, Any],
    paths: dict[str, Path],
    analysis: dict[str, Any],
    intervention_applied: bool,
) -> None:
    record = {
        "event": "prompt_submit",
        "timestamp": utc_now(),
        "session_id": payload.get("session_id"),
        "mode": config.get("mode"),
        "hook_event_name": payload.get("hook_event_name"),
        "hostile_detected": analysis["hostile"],
        "hostility_score": analysis["score"],
        "hostility_threshold": analysis["threshold"],
        "matched_rule_ids": analysis["matched_rule_ids"],
        "prompt_length_bucket": analysis["prompt_length_bucket"],
        "intervention_applied": intervention_applied,
        "intervention_template_id": config.get("mode") if intervention_applied else None,
    }
    append_jsonl(paths["metrics"], record)


def log_session_summary(
    payload: dict[str, Any],
    config: dict[str, Any],
    paths: dict[str, Path],
    state: dict[str, Any],
) -> None:
    record = {
        "event": "session_summary",
        "timestamp": utc_now(),
        "session_id": payload.get("session_id"),
        "mode": state.get("mode", config.get("mode")),
        "hook_event_name": payload.get("hook_event_name"),
        "session_end_reason": payload.get("reason"),
        "total_prompts": state.get("total_prompts", 0),
        "hostile_prompts": state.get("hostile_prompts", 0),
        "interventions_applied": state.get("interventions_applied", 0),
        "rule_counts": state.get("rule_counts", {}),
        "started_at": state.get("started_at"),
        "ended_at": utc_now(),
    }
    append_jsonl(paths["metrics"], record)


def handle_session_start(payload: dict[str, Any], env: dict[str, str] | None = None) -> dict[str, Any] | None:
    plugin_root, project_root = resolve_paths(payload, env)
    config = load_config(plugin_root)
    if not config.get("logging_enabled", True):
        return None

    paths = get_log_paths(project_root, config)
    ensure_log_dirs(paths)
    state_path = session_state_path(paths, str(payload.get("session_id", "unknown")))
    state = load_state(state_path, payload, str(config.get("mode")))
    state["mode"] = config.get("mode")
    state["last_event_at"] = utc_now()
    save_state(state_path, state)
    log_session_start(payload, config, paths)
    return None


def handle_user_prompt_submit(
    payload: dict[str, Any],
    env: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    plugin_root, project_root = resolve_paths(payload, env)
    config = load_config(plugin_root)
    prompt = str(payload.get("prompt", ""))
    analysis = analyze_hostility(prompt, config)
    mode = str(config.get("mode"))
    additional_context = build_additional_context(mode, config) if analysis["hostile"] else None
    intervention_applied = bool(additional_context)

    if config.get("logging_enabled", True):
        paths = get_log_paths(project_root, config)
        ensure_log_dirs(paths)
        state_path = session_state_path(paths, str(payload.get("session_id", "unknown")))
        state = load_state(state_path, payload, mode)
        state["mode"] = mode
        state["total_prompts"] += 1
        if analysis["hostile"]:
            state["hostile_prompts"] += 1
        if intervention_applied:
            state["interventions_applied"] += 1
        rule_counts = Counter(state.get("rule_counts", {}))
        rule_counts.update(analysis["matched_rule_ids"])
        state["rule_counts"] = dict(sorted(rule_counts.items()))
        state["last_event_at"] = utc_now()
        save_state(state_path, state)
        log_prompt_submit(payload, config, paths, analysis, intervention_applied)

    if not intervention_applied:
        return None

    return {
        "suppressOutput": True,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional_context,
        },
    }


def handle_session_end(payload: dict[str, Any], env: dict[str, str] | None = None) -> dict[str, Any] | None:
    plugin_root, project_root = resolve_paths(payload, env)
    config = load_config(plugin_root)
    if not config.get("logging_enabled", True):
        return None

    paths = get_log_paths(project_root, config)
    ensure_log_dirs(paths)
    state_path = session_state_path(paths, str(payload.get("session_id", "unknown")))
    state = load_state(state_path, payload, str(config.get("mode")))
    log_session_summary(payload, config, paths, state)
    if state_path.exists():
        state_path.unlink()
    return None


def dispatch_hook(payload: dict[str, Any], env: dict[str, str] | None = None) -> dict[str, Any] | None:
    event = payload.get("hook_event_name")
    if event == "SessionStart":
        return handle_session_start(payload, env)
    if event == "UserPromptSubmit":
        return handle_user_prompt_submit(payload, env)
    if event == "SessionEnd":
        return handle_session_end(payload, env)
    return None


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    prompts_by_mode: dict[str, Counter[str]] = defaultdict(Counter)
    sessions_by_mode: dict[str, Counter[str]] = defaultdict(Counter)
    scores_by_mode: dict[str, list[float]] = defaultdict(list)
    rule_counts_by_mode: dict[str, Counter[str]] = defaultdict(Counter)

    for record in records:
        mode = str(record.get("mode", "unknown"))
        if record.get("event") == "prompt_submit":
            prompts_by_mode[mode]["prompts"] += 1
            if record.get("hostile_detected"):
                prompts_by_mode[mode]["hostile_prompts"] += 1
            if record.get("intervention_applied"):
                prompts_by_mode[mode]["interventions_applied"] += 1
            scores_by_mode[mode].append(float(record.get("hostility_score", 0.0)))
            rule_counts_by_mode[mode].update(record.get("matched_rule_ids", []))
        elif record.get("event") == "session_summary":
            sessions_by_mode[mode]["sessions"] += 1
            sessions_by_mode[mode]["hostile_prompts"] += int(record.get("hostile_prompts", 0))
            sessions_by_mode[mode]["interventions_applied"] += int(record.get("interventions_applied", 0))

    modes = sorted(set(prompts_by_mode) | set(sessions_by_mode))
    return {
        "modes": modes,
        "prompts_by_mode": {mode: dict(counter) for mode, counter in prompts_by_mode.items()},
        "sessions_by_mode": {mode: dict(counter) for mode, counter in sessions_by_mode.items()},
        "average_scores": {
            mode: round(sum(scores) / len(scores), 3) if scores else 0.0
            for mode, scores in scores_by_mode.items()
        },
        "rule_counts_by_mode": {
            mode: dict(counter.most_common()) for mode, counter in rule_counts_by_mode.items()
        },
    }
