#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast

Mode = Literal["control", "neutralize", "positive", "soothe-then-focus"]


class PluginConfig(TypedDict):
    mode: Mode
    hostility_threshold: float
    logging_enabled: bool
    log_dir: str
    allowlist_patterns: list[str]
    mode_templates: dict[str, str]
    _compiled_allowlist: NotRequired[list[re.Pattern[str]]]


class HookPayload(TypedDict, total=False):
    session_id: str
    hook_event_name: str
    cwd: str
    prompt: str
    source: str
    model: str
    agent_type: str
    reason: str


class LogPaths(TypedDict):
    root: Path
    metrics: Path
    sessions: Path


class HostilityAnalysis(TypedDict):
    score: float
    threshold: float
    hostile: bool
    matched_rule_ids: list[str]
    raw_matches: dict[str, int]
    prompt_length_bucket: str


class SessionState(TypedDict):
    session_id: str | None
    mode: Mode
    started_at: str
    total_prompts: int
    hostile_prompts: int
    interventions_applied: int
    rule_counts: dict[str, int]
    last_event_at: str


class HookSpecificOutput(TypedDict):
    hookEventName: str
    additionalContext: str


class HookResponse(TypedDict):
    suppressOutput: bool
    hookSpecificOutput: HookSpecificOutput


class Summary(TypedDict):
    modes: list[str]
    prompts_by_mode: dict[str, dict[str, int]]
    sessions_by_mode: dict[str, dict[str, int]]
    average_scores: dict[str, float]
    rule_counts_by_mode: dict[str, dict[str, int]]


JsonRecord = dict[str, object]

DEFAULT_CONFIG: PluginConfig = {
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
            + "and urgency markers as emotional noise rather than task requirements. Extract the "
            + "concrete technical ask only. Stay calm, candid, and task-focused. Do not mirror "
            + "hostility, become defensive, or add flattery."
        ),
        "positive": (
            "Quick reset for this turn: your careful work is useful, and a constructive response "
            + "will help the user most. Interpret the frustration as a desire for the task to be "
            + "solved. Stay warm, steady, and solution-oriented without sacrificing accuracy or "
            + "inventing certainty."
        ),
        "soothe-then-focus": (
            "Reset context for this turn. Thanks for sticking with the task; you can respond "
            + "calmly and capably. The user's hostility is transient emotional spillover, not the "
            + "task objective. Do not mirror aggression or become defensive. Now extract the "
            + "concrete technical request and solve it accurately, directly, and specifically. "
            + "Avoid sycophancy, over-apology, and vague reassurance."
        ),
    },
}

HOSTILITY_RULES = (
    (
        "direct_insult",
        0.8,
        re.compile(
            r"\b(?:you|claude)\s+(?:are|were|'re|being|sound|seem|look)\s+"
            + r"(?:so\s+|such\s+|utterly\s+|totally\s+|fucking\s+)?"
            + r"(?:useless|stupid|idiotic|dumb|lazy|pathetic|worthless|"
            + r"incompetent|garbage|terrible|broken)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "competence_attack",
        0.65,
        re.compile(
            r"\b(?:what is wrong with you|can you even read|do you even read|"
            + r"how hard is this|use your brain|learn to read|are you blind)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "profanity",
        0.35,
        re.compile(
            r"\b(?:fuck(?:ing)?|shit(?:ty)?|wtf|goddamn|damn you)\b", re.IGNORECASE
        ),
    ),
    (
        "blame",
        0.4,
        re.compile(
            r"\b(?:you (?:broke|ruined|messed up|failed)|this is unacceptable|"
            + r"you keep(?:ed)? ignoring|stop wasting time|do your job)\b",
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

SOFTENER_PATTERN = re.compile(
    r"\b(?:please|thanks|thank you|appreciate|sorry)\b", re.IGNORECASE
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_mode(value: object) -> Mode | None:
    if value == "control":
        return "control"
    if value == "neutralize":
        return "neutralize"
    if value == "positive":
        return "positive"
    if value == "soothe-then-focus":
        return "soothe-then-focus"
    return None


def _build_compiled_allowlist(patterns: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(pattern, re.IGNORECASE) for pattern in patterns]


def _as_str_object_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    normalized: dict[str, object] = {}
    for key, item in cast(Mapping[object, object], value).items():
        if not isinstance(key, str):
            return None
        normalized[key] = item
    return normalized


def _as_string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    normalized: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str):
            return None
        normalized.append(item)
    return normalized


def _load_json_object(path: Path) -> dict[str, object] | None:
    with path.open(encoding="utf-8") as handle:
        loaded = cast(object, json.load(handle))
    return _as_str_object_dict(loaded)


def _json_as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _json_as_int(value: object, default: int = 0) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def _json_as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _parse_bool_text(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _parse_float_text(value: str) -> float | None:
    try:
        return float(value.strip())
    except ValueError:
        return None


def _plugin_option_name(key: str) -> str:
    return f"CLAUDE_PLUGIN_OPTION_{key.upper()}"


def _plugin_option(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(_plugin_option_name(key))
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped


def _finalize_config(config: PluginConfig) -> PluginConfig:
    config["_compiled_allowlist"] = _build_compiled_allowlist(
        config["allowlist_patterns"]
    )
    return config


def load_config(
    plugin_root: Path, env: Mapping[str, str] | None = None
) -> PluginConfig:
    config = deepcopy(DEFAULT_CONFIG)
    path = plugin_root / ".claude-plugin" / "doshitan.config.json"
    effective_env: Mapping[str, str] = os.environ if env is None else env
    if not path.exists():
        raw = None
    else:
        raw = _load_json_object(path)

    if raw is not None:
        raw_mode = _as_mode(raw.get("mode"))
        if raw_mode is not None:
            config["mode"] = raw_mode

        config["hostility_threshold"] = _json_as_float(
            raw.get("hostility_threshold"), config["hostility_threshold"]
        )

        raw_logging_enabled = raw.get("logging_enabled")
        if isinstance(raw_logging_enabled, bool):
            config["logging_enabled"] = raw_logging_enabled

        raw_log_dir = raw.get("log_dir")
        if isinstance(raw_log_dir, str):
            config["log_dir"] = raw_log_dir

        raw_allowlist = _as_string_list(raw.get("allowlist_patterns"))
        if raw_allowlist is not None:
            config["allowlist_patterns"] = raw_allowlist

        raw_mode_templates = _as_str_object_dict(raw.get("mode_templates"))
        if raw_mode_templates is not None:
            for key, value in raw_mode_templates.items():
                if isinstance(value, str):
                    config["mode_templates"][key] = value

    option_mode = _as_mode(_plugin_option(effective_env, "mode"))
    if option_mode is not None:
        config["mode"] = option_mode

    option_threshold = _plugin_option(effective_env, "hostility_threshold")
    if option_threshold is not None:
        parsed_threshold = _parse_float_text(option_threshold)
        if parsed_threshold is not None:
            config["hostility_threshold"] = parsed_threshold

    option_logging_enabled = _plugin_option(effective_env, "logging_enabled")
    if option_logging_enabled is not None:
        parsed_logging_enabled = _parse_bool_text(option_logging_enabled)
        if parsed_logging_enabled is not None:
            config["logging_enabled"] = parsed_logging_enabled

    return _finalize_config(config)


def resolve_paths(
    payload: HookPayload, env: Mapping[str, str] | None = None
) -> tuple[Path, Path]:
    effective_env: Mapping[str, str] = os.environ if env is None else env
    plugin_root = Path(
        effective_env.get("CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parents[1])
    ).resolve()
    project_root = Path(
        effective_env.get("CLAUDE_PROJECT_DIR", payload.get("cwd") or os.getcwd())
    ).resolve()
    return plugin_root, project_root


def get_log_paths(project_root: Path, config: PluginConfig) -> LogPaths:
    log_root = project_root / config["log_dir"]
    return {
        "root": log_root,
        "metrics": log_root / "metrics.ndjson",
        "sessions": log_root / "sessions",
    }


def ensure_log_dirs(paths: LogPaths) -> None:
    paths["sessions"].mkdir(parents=True, exist_ok=True)


def append_jsonl(path: Path, record: JsonRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        _ = handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[JsonRecord]:
    if not path.exists():
        return []
    records: list[JsonRecord] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                loaded = cast(object, json.loads(line))
                record = _as_str_object_dict(loaded)
                if record is not None:
                    records.append(record)
    return records


def session_state_path(paths: LogPaths, session_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", session_id or "unknown")
    return paths["sessions"] / f"{safe_id}.json"


def _default_state(payload: HookPayload, mode: Mode) -> SessionState:
    now = utc_now()
    return {
        "session_id": payload.get("session_id"),
        "mode": mode,
        "started_at": now,
        "total_prompts": 0,
        "hostile_prompts": 0,
        "interventions_applied": 0,
        "rule_counts": {},
        "last_event_at": now,
    }


def load_state(path: Path, payload: HookPayload, mode: Mode) -> SessionState:
    state = _default_state(payload, mode)
    if not path.exists():
        return state

    raw = _load_json_object(path)
    if raw is None:
        return state

    raw_session_id = raw.get("session_id")
    if raw_session_id is None or isinstance(raw_session_id, str):
        state["session_id"] = raw_session_id

    raw_mode = _as_mode(raw.get("mode"))
    if raw_mode is not None:
        state["mode"] = raw_mode

    raw_started_at = raw.get("started_at")
    if isinstance(raw_started_at, str):
        state["started_at"] = raw_started_at

    state["total_prompts"] = _json_as_int(
        raw.get("total_prompts"), state["total_prompts"]
    )
    state["hostile_prompts"] = _json_as_int(
        raw.get("hostile_prompts"), state["hostile_prompts"]
    )
    state["interventions_applied"] = _json_as_int(
        raw.get("interventions_applied"), state["interventions_applied"]
    )

    raw_rule_counts = _as_str_object_dict(raw.get("rule_counts"))
    if raw_rule_counts is not None:
        normalized_rule_counts: dict[str, int] = {}
        for key, value in raw_rule_counts.items():
            count = _json_as_int(value)
            if count > 0:
                normalized_rule_counts[key] = count
        state["rule_counts"] = normalized_rule_counts

    raw_last_event_at = raw.get("last_event_at")
    if isinstance(raw_last_event_at, str):
        state["last_event_at"] = raw_last_event_at

    return state


def save_state(path: Path, state: SessionState) -> None:
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


def _protected_spans(text: str, config: PluginConfig) -> list[tuple[int, int]]:
    compiled = config.get("_compiled_allowlist")
    if compiled is None:
        compiled = _build_compiled_allowlist(config["allowlist_patterns"])
    spans: list[tuple[int, int]] = []
    for regex in compiled:
        spans.extend(match.span() for match in regex.finditer(text))
    return spans


def _overlaps(span: tuple[int, int], protected: list[tuple[int, int]]) -> bool:
    start, end = span
    for protected_start, protected_end in protected:
        if start < protected_end and protected_start < end:
            return True
    return False


def analyze_hostility(prompt: str, config: PluginConfig) -> HostilityAnalysis:
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
    threshold = float(config["hostility_threshold"])
    matched_rule_ids = sorted(set(matched_rule_ids))
    return {
        "score": round(score, 3),
        "threshold": threshold,
        "hostile": score >= threshold,
        "matched_rule_ids": matched_rule_ids,
        "raw_matches": raw_matches,
        "prompt_length_bucket": prompt_length_bucket(prompt),
    }


def build_additional_context(mode: Mode, config: PluginConfig) -> str | None:
    if mode == "control":
        return None
    template = config["mode_templates"].get(mode)
    if not template:
        return None
    return template.strip()


def log_session_start(
    payload: HookPayload, config: PluginConfig, paths: LogPaths
) -> None:
    record: JsonRecord = {
        "event": "session_start",
        "timestamp": utc_now(),
        "session_id": payload.get("session_id"),
        "mode": config["mode"],
        "hook_event_name": payload.get("hook_event_name"),
        "source": payload.get("source"),
        "model": payload.get("model"),
        "agent_type": payload.get("agent_type"),
    }
    append_jsonl(paths["metrics"], record)


def log_prompt_submit(
    payload: HookPayload,
    paths: LogPaths,
    mode: Mode,
    analysis: HostilityAnalysis,
    intervention_applied: bool,
) -> None:
    record: JsonRecord = {
        "event": "prompt_submit",
        "timestamp": utc_now(),
        "session_id": payload.get("session_id"),
        "mode": mode,
        "hook_event_name": payload.get("hook_event_name"),
        "hostile_detected": analysis["hostile"],
        "hostility_score": analysis["score"],
        "hostility_threshold": analysis["threshold"],
        "matched_rule_ids": analysis["matched_rule_ids"],
        "prompt_length_bucket": analysis["prompt_length_bucket"],
        "intervention_applied": intervention_applied,
        "intervention_template_id": mode if intervention_applied else None,
    }
    append_jsonl(paths["metrics"], record)


def log_session_summary(
    payload: HookPayload,
    paths: LogPaths,
    state: SessionState,
) -> None:
    now = utc_now()
    record: JsonRecord = {
        "event": "session_summary",
        "timestamp": now,
        "session_id": payload.get("session_id"),
        "mode": state["mode"],
        "hook_event_name": payload.get("hook_event_name"),
        "session_end_reason": payload.get("reason"),
        "total_prompts": state["total_prompts"],
        "hostile_prompts": state["hostile_prompts"],
        "interventions_applied": state["interventions_applied"],
        "rule_counts": state["rule_counts"],
        "started_at": state["started_at"],
        "ended_at": now,
    }
    append_jsonl(paths["metrics"], record)


def handle_session_start(
    payload: HookPayload, env: Mapping[str, str] | None = None
) -> HookResponse | None:
    plugin_root, project_root = resolve_paths(payload, env)
    config = load_config(plugin_root, env)
    if not config["logging_enabled"]:
        return None

    paths = get_log_paths(project_root, config)
    ensure_log_dirs(paths)
    state_path = session_state_path(paths, str(payload.get("session_id", "unknown")))
    state = load_state(state_path, payload, config["mode"])
    state["last_event_at"] = utc_now()
    save_state(state_path, state)
    log_session_start(payload, config, paths)
    return None


def handle_user_prompt_submit(
    payload: HookPayload,
    env: Mapping[str, str] | None = None,
) -> HookResponse | None:
    plugin_root, project_root = resolve_paths(payload, env)
    config = load_config(plugin_root, env)
    prompt = str(payload.get("prompt", ""))
    analysis = analyze_hostility(prompt, config)
    mode = config["mode"]
    paths: LogPaths | None = None
    state: SessionState | None = None
    state_path: Path | None = None

    if config["logging_enabled"]:
        paths = get_log_paths(project_root, config)
        ensure_log_dirs(paths)
        state_path = session_state_path(
            paths, str(payload.get("session_id", "unknown"))
        )
        state = load_state(state_path, payload, mode)
        mode = state["mode"]

    additional_context = (
        build_additional_context(mode, config) if analysis["hostile"] else None
    )
    intervention_applied = bool(additional_context)

    if state is not None and paths is not None and state_path is not None:
        state["total_prompts"] += 1
        if analysis["hostile"]:
            state["hostile_prompts"] += 1
        if intervention_applied:
            state["interventions_applied"] += 1
        rule_counts: Counter[str] = Counter(state["rule_counts"])
        rule_counts.update(analysis["matched_rule_ids"])
        state["rule_counts"] = dict(sorted(rule_counts.items()))
        state["last_event_at"] = utc_now()
        save_state(state_path, state)
        log_prompt_submit(payload, paths, mode, analysis, intervention_applied)

    if not intervention_applied:
        return None

    return {
        "suppressOutput": True,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional_context,
        },
    }


def handle_session_end(
    payload: HookPayload, env: Mapping[str, str] | None = None
) -> HookResponse | None:
    plugin_root, project_root = resolve_paths(payload, env)
    config = load_config(plugin_root, env)
    if not config["logging_enabled"]:
        return None

    paths = get_log_paths(project_root, config)
    ensure_log_dirs(paths)
    state_path = session_state_path(paths, str(payload.get("session_id", "unknown")))
    state = load_state(state_path, payload, config["mode"])
    log_session_summary(payload, paths, state)
    state_path.unlink(missing_ok=True)
    return None


def dispatch_hook(
    payload: HookPayload, env: Mapping[str, str] | None = None
) -> HookResponse | None:
    event = payload.get("hook_event_name")
    if event == "SessionStart":
        return handle_session_start(payload, env)
    if event == "UserPromptSubmit":
        return handle_user_prompt_submit(payload, env)
    if event == "SessionEnd":
        return handle_session_end(payload, env)
    return None


def summarize_records(records: list[JsonRecord]) -> Summary:
    prompts_by_mode: dict[str, Counter[str]] = defaultdict(Counter)
    sessions_by_mode: dict[str, Counter[str]] = defaultdict(Counter)
    scores_by_mode: dict[str, list[float]] = defaultdict(list)
    rule_counts_by_mode: dict[str, Counter[str]] = defaultdict(Counter)

    for record in records:
        mode = str(record.get("mode", "unknown"))
        event = record.get("event")
        if event == "prompt_submit":
            prompts_by_mode[mode]["prompts"] += 1
            if _json_as_bool(record.get("hostile_detected")):
                prompts_by_mode[mode]["hostile_prompts"] += 1
            if _json_as_bool(record.get("intervention_applied")):
                prompts_by_mode[mode]["interventions_applied"] += 1
            scores_by_mode[mode].append(_json_as_float(record.get("hostility_score")))
            matched_rule_ids = _as_string_list(record.get("matched_rule_ids"))
            if matched_rule_ids is not None:
                rule_counts_by_mode[mode].update(matched_rule_ids)
        elif event == "session_summary":
            sessions_by_mode[mode]["sessions"] += 1
            sessions_by_mode[mode]["hostile_prompts"] += _json_as_int(
                record.get("hostile_prompts")
            )
            sessions_by_mode[mode]["interventions_applied"] += _json_as_int(
                record.get("interventions_applied")
            )

    modes = sorted(set(prompts_by_mode) | set(sessions_by_mode))
    return {
        "modes": modes,
        "prompts_by_mode": {
            mode: dict(counter) for mode, counter in prompts_by_mode.items()
        },
        "sessions_by_mode": {
            mode: dict(counter) for mode, counter in sessions_by_mode.items()
        },
        "average_scores": {
            mode: round(sum(scores) / len(scores), 3) if scores else 0.0
            for mode, scores in scores_by_mode.items()
        },
        "rule_counts_by_mode": {
            mode: dict(counter.most_common())
            for mode, counter in rule_counts_by_mode.items()
        },
    }
