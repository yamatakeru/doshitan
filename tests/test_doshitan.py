from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

_ = sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from doshitan_core import (  # noqa: E402
    DEFAULT_CONFIG,
    HookPayload,
    PluginConfig,
    analyze_hostility,
    dispatch_hook,
    load_state,
    read_jsonl,
)


def _make_env(
    plugin_dir: str,
    project_dir: str,
    mode: str = "soothe-then-focus",
    *,
    hostility_threshold: float = 0.75,
    logging_enabled: bool = True,
    config_overrides: dict[str, object] | None = None,
) -> dict[str, str]:
    plugin_root = Path(plugin_dir)
    (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    config: dict[str, object] = {
        "log_dir": ".claude/doshitan",
    }
    if config_overrides is not None:
        config.update(config_overrides)
    _ = (plugin_root / ".claude-plugin" / "doshitan.config.json").write_text(
        json.dumps(config),
        encoding="utf-8",
    )
    return {
        "CLAUDE_PLUGIN_ROOT": str(plugin_root),
        "CLAUDE_PROJECT_DIR": project_dir,
        "CLAUDE_PLUGIN_OPTION_MODE": mode,
        "CLAUDE_PLUGIN_OPTION_HOSTILITY_THRESHOLD": str(hostility_threshold),
        "CLAUDE_PLUGIN_OPTION_LOGGING_ENABLED": (
            "true" if logging_enabled else "false"
        ),
    }


def _config(*, allowlist_patterns: list[str] | None = None) -> PluginConfig:
    config = deepcopy(DEFAULT_CONFIG)
    config["hostility_threshold"] = 0.75
    config["allowlist_patterns"] = allowlist_patterns or []
    _ = config.pop("_compiled_allowlist", None)
    return config


def _payload(
    *,
    session_id: str,
    hook_event_name: str,
    cwd: str,
    prompt: str | None = None,
    source: str | None = None,
    model: str | None = None,
    reason: str | None = None,
) -> HookPayload:
    payload: HookPayload = {
        "session_id": session_id,
        "hook_event_name": hook_event_name,
        "cwd": cwd,
    }
    if prompt is not None:
        payload["prompt"] = prompt
    if source is not None:
        payload["source"] = source
    if model is not None:
        payload["model"] = model
    if reason is not None:
        payload["reason"] = reason
    return payload


class DoshitanTests(unittest.TestCase):
    def test_direct_insult_is_detected(self) -> None:
        config = _config()
        analysis = analyze_hostility("You are useless, fix this now!!!", config)
        self.assertTrue(analysis["hostile"])
        self.assertIn("direct_insult", analysis["matched_rule_ids"])

    def test_technical_allowlist_reduces_false_positive(self) -> None:
        config = _config(
            allowlist_patterns=[
                r"\bgarbage collector\b",
                r"\bkill the process\b",
            ]
        )
        analysis = analyze_hostility(
            "Please inspect the garbage collector and kill the process if it deadlocks.",
            config,
        )
        self.assertFalse(analysis["hostile"])
        self.assertEqual(analysis["matched_rule_ids"], [])

    def test_soothe_then_focus_returns_hidden_context(self) -> None:
        with (
            tempfile.TemporaryDirectory() as plugin_dir,
            tempfile.TemporaryDirectory() as project_dir,
        ):
            env = _make_env(plugin_dir, project_dir, mode="soothe-then-focus")
            payload = _payload(
                session_id="abc123",
                hook_event_name="UserPromptSubmit",
                cwd=str(project_dir),
                prompt="You are useless, fix this now!!!",
            )
            result = dispatch_hook(payload, env)
            self.assertIsNotNone(result)
            assert result is not None
            context = result["hookSpecificOutput"]["additionalContext"]
            self.assertIn("Do not mirror aggression", context)
            self.assertIn("solve it accurately", context)

    def test_control_mode_logs_without_intervention(self) -> None:
        with (
            tempfile.TemporaryDirectory() as plugin_dir,
            tempfile.TemporaryDirectory() as project_dir,
        ):
            project_root = Path(project_dir)
            env = _make_env(plugin_dir, project_dir, mode="control")
            _ = dispatch_hook(
                _payload(
                    session_id="session-1",
                    hook_event_name="SessionStart",
                    cwd=str(project_root),
                    source="startup",
                    model="sonnet",
                ),
                env,
            )
            result = dispatch_hook(
                _payload(
                    session_id="session-1",
                    hook_event_name="UserPromptSubmit",
                    cwd=str(project_root),
                    prompt="You are broken, fix this now!!!",
                ),
                env,
            )
            self.assertIsNone(result)

            metrics_path = project_root / ".claude" / "doshitan" / "metrics.ndjson"
            self.assertTrue(metrics_path.exists())
            prompt_record = read_jsonl(metrics_path)[-1]
            self.assertEqual(prompt_record["event"], "prompt_submit")
            self.assertTrue(prompt_record["hostile_detected"])
            self.assertFalse(prompt_record["intervention_applied"])

    def test_logging_can_be_disabled_via_user_option(self) -> None:
        with (
            tempfile.TemporaryDirectory() as plugin_dir,
            tempfile.TemporaryDirectory() as project_dir,
        ):
            project_root = Path(project_dir)
            env = _make_env(
                plugin_dir,
                project_dir,
                mode="soothe-then-focus",
                logging_enabled=False,
            )
            result = dispatch_hook(
                _payload(
                    session_id="session-no-log",
                    hook_event_name="UserPromptSubmit",
                    cwd=str(project_root),
                    prompt="You are useless, fix this now!!!",
                ),
                env,
            )

            self.assertIsNotNone(result)
            metrics_path = project_root / ".claude" / "doshitan" / "metrics.ndjson"
            self.assertFalse(metrics_path.exists())

    def test_session_end_writes_summary_and_removes_state(self) -> None:
        with (
            tempfile.TemporaryDirectory() as plugin_dir,
            tempfile.TemporaryDirectory() as project_dir,
        ):
            project_root = Path(project_dir)
            env = _make_env(plugin_dir, project_dir, mode="neutralize")
            start_payload = _payload(
                session_id="session-2",
                hook_event_name="SessionStart",
                cwd=str(project_root),
                source="startup",
                model="sonnet",
            )
            prompt_payload = _payload(
                session_id="session-2",
                hook_event_name="UserPromptSubmit",
                cwd=str(project_root),
                prompt="What is wrong with you? Fix this now!!!",
            )
            end_payload = _payload(
                session_id="session-2",
                hook_event_name="SessionEnd",
                cwd=str(project_root),
                reason="other",
            )
            _ = dispatch_hook(start_payload, env)
            _ = dispatch_hook(prompt_payload, env)
            _ = dispatch_hook(end_payload, env)

            metrics_path = project_root / ".claude" / "doshitan" / "metrics.ndjson"
            records = read_jsonl(metrics_path)
            self.assertEqual(records[-1]["event"], "session_summary")
            state_path = (
                project_root / ".claude" / "doshitan" / "sessions" / "session-2.json"
            )
            self.assertFalse(state_path.exists())

    def test_session_mode_stays_stable_after_config_change(self) -> None:
        with (
            tempfile.TemporaryDirectory() as plugin_dir,
            tempfile.TemporaryDirectory() as project_dir,
        ):
            plugin_root = Path(plugin_dir)
            project_root = Path(project_dir)
            env = _make_env(plugin_dir, project_dir, mode="neutralize")

            _ = dispatch_hook(
                _payload(
                    session_id="session-3",
                    hook_event_name="SessionStart",
                    cwd=str(project_root),
                ),
                env,
            )

            _ = (plugin_root / ".claude-plugin" / "doshitan.config.json").write_text(
                json.dumps(
                    {
                        "mode": "positive",
                        "logging_enabled": True,
                        "hostility_threshold": 0.75,
                        "log_dir": ".claude/doshitan",
                    }
                ),
                encoding="utf-8",
            )

            result = dispatch_hook(
                _payload(
                    session_id="session-3",
                    hook_event_name="UserPromptSubmit",
                    cwd=str(project_root),
                    prompt="You are useless, fix this now!!!",
                ),
                env,
            )
            _ = dispatch_hook(
                _payload(
                    session_id="session-3",
                    hook_event_name="SessionEnd",
                    cwd=str(project_root),
                    reason="other",
                ),
                env,
            )

            self.assertIsNotNone(result)
            assert result is not None
            context = result["hookSpecificOutput"]["additionalContext"]
            self.assertIn("Stay calm, candid, and task-focused.", context)
            self.assertNotIn("Quick reset for this turn", context)

            metrics_path = project_root / ".claude" / "doshitan" / "metrics.ndjson"
            records = read_jsonl(metrics_path)
            self.assertEqual(records[1]["event"], "prompt_submit")
            self.assertEqual(records[1]["mode"], "neutralize")
            self.assertEqual(records[1]["intervention_template_id"], "neutralize")
            self.assertEqual(records[2]["event"], "session_summary")
            self.assertEqual(records[2]["mode"], "neutralize")

    def test_load_state_discards_invalid_rule_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "session.json"
            _ = state_path.write_text(
                json.dumps(
                    {
                        "session_id": "session-4",
                        "mode": "neutralize",
                        "started_at": "2026-04-07T00:00:00+00:00",
                        "total_prompts": 3,
                        "hostile_prompts": 2,
                        "interventions_applied": 2,
                        "rule_counts": {
                            "direct_insult": 2,
                            "blame": 0,
                            "profanity": -1,
                            "shouting": True,
                            "garbage": "3",
                        },
                        "last_event_at": "2026-04-07T00:00:01+00:00",
                    }
                ),
                encoding="utf-8",
            )

            state = load_state(
                state_path,
                _payload(
                    session_id="session-4",
                    hook_event_name="SessionStart",
                    cwd=tmp_dir,
                ),
                "neutralize",
            )

            self.assertEqual(state["rule_counts"], {"direct_insult": 2})

    def test_user_option_overrides_file_config(self) -> None:
        with (
            tempfile.TemporaryDirectory() as plugin_dir,
            tempfile.TemporaryDirectory() as project_dir,
        ):
            env = _make_env(
                plugin_dir,
                project_dir,
                mode="positive",
                config_overrides={
                    "mode": "neutralize",
                    "hostility_threshold": 0.95,
                    "logging_enabled": True,
                },
            )

            result = dispatch_hook(
                _payload(
                    session_id="session-override",
                    hook_event_name="UserPromptSubmit",
                    cwd=str(project_dir),
                    prompt="You are useless, fix this now!!!",
                ),
                env,
            )

            self.assertIsNotNone(result)
            assert result is not None
            context = result["hookSpecificOutput"]["additionalContext"]
            self.assertIn("Quick reset for this turn", context)
            self.assertNotIn("Stay calm, candid, and task-focused.", context)


if __name__ == "__main__":
    _ = unittest.main()
