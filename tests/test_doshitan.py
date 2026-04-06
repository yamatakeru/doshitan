from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from doshitan_core import analyze_hostility, dispatch_hook  # noqa: E402


class DoshitanTests(unittest.TestCase):
    def test_direct_insult_is_detected(self) -> None:
        config = {
            "hostility_threshold": 0.75,
            "allowlist_patterns": [],
        }
        analysis = analyze_hostility("You are useless, fix this now!!!", config)
        self.assertTrue(analysis["hostile"])
        self.assertIn("direct_insult", analysis["matched_rule_ids"])

    def test_technical_allowlist_reduces_false_positive(self) -> None:
        config = {
            "hostility_threshold": 0.75,
            "allowlist_patterns": [
                r"\bgarbage collector\b",
                r"\bkill the process\b",
            ],
        }
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
            plugin_root = Path(plugin_dir)
            (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
            (plugin_root / ".claude-plugin" / "doshitan.config.json").write_text(
                json.dumps(
                    {
                        "mode": "soothe-then-focus",
                        "logging_enabled": True,
                        "hostility_threshold": 0.75,
                        "log_dir": ".claude/doshitan",
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "CLAUDE_PLUGIN_ROOT": str(plugin_root),
                "CLAUDE_PROJECT_DIR": str(project_dir),
            }
            payload = {
                "session_id": "abc123",
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(project_dir),
                "prompt": "You are useless, fix this now!!!",
            }
            result = dispatch_hook(payload, env)
            self.assertIsNotNone(result)
            context = result["hookSpecificOutput"]["additionalContext"]
            self.assertIn("Do not mirror aggression", context)
            self.assertIn("solve it accurately", context)

    def test_control_mode_logs_without_intervention(self) -> None:
        with (
            tempfile.TemporaryDirectory() as plugin_dir,
            tempfile.TemporaryDirectory() as project_dir,
        ):
            plugin_root = Path(plugin_dir)
            project_root = Path(project_dir)
            (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
            (plugin_root / ".claude-plugin" / "doshitan.config.json").write_text(
                json.dumps(
                    {
                        "mode": "control",
                        "logging_enabled": True,
                        "hostility_threshold": 0.75,
                        "log_dir": ".claude/doshitan",
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "CLAUDE_PLUGIN_ROOT": str(plugin_root),
                "CLAUDE_PROJECT_DIR": str(project_root),
            }
            dispatch_hook(
                {
                    "session_id": "session-1",
                    "hook_event_name": "SessionStart",
                    "cwd": str(project_root),
                    "source": "startup",
                    "model": "sonnet",
                },
                env,
            )
            result = dispatch_hook(
                {
                    "session_id": "session-1",
                    "hook_event_name": "UserPromptSubmit",
                    "cwd": str(project_root),
                    "prompt": "You are broken, fix this now!!!",
                },
                env,
            )
            self.assertIsNone(result)

            metrics_path = project_root / ".claude" / "doshitan" / "metrics.ndjson"
            self.assertTrue(metrics_path.exists())
            lines = metrics_path.read_text(encoding="utf-8").strip().splitlines()
            prompt_record = json.loads(lines[-1])
            self.assertEqual(prompt_record["event"], "prompt_submit")
            self.assertTrue(prompt_record["hostile_detected"])
            self.assertFalse(prompt_record["intervention_applied"])

    def test_session_end_writes_summary_and_removes_state(self) -> None:
        with (
            tempfile.TemporaryDirectory() as plugin_dir,
            tempfile.TemporaryDirectory() as project_dir,
        ):
            plugin_root = Path(plugin_dir)
            project_root = Path(project_dir)
            (plugin_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
            (plugin_root / ".claude-plugin" / "doshitan.config.json").write_text(
                json.dumps(
                    {
                        "mode": "neutralize",
                        "logging_enabled": True,
                        "hostility_threshold": 0.75,
                        "log_dir": ".claude/doshitan",
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "CLAUDE_PLUGIN_ROOT": str(plugin_root),
                "CLAUDE_PROJECT_DIR": str(project_root),
            }
            start_payload = {
                "session_id": "session-2",
                "hook_event_name": "SessionStart",
                "cwd": str(project_root),
                "source": "startup",
                "model": "sonnet",
            }
            prompt_payload = {
                "session_id": "session-2",
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(project_root),
                "prompt": "What is wrong with you? Fix this now!!!",
            }
            end_payload = {
                "session_id": "session-2",
                "hook_event_name": "SessionEnd",
                "cwd": str(project_root),
                "reason": "other",
            }
            dispatch_hook(start_payload, env)
            dispatch_hook(prompt_payload, env)
            dispatch_hook(end_payload, env)

            metrics_path = project_root / ".claude" / "doshitan" / "metrics.ndjson"
            records = [
                json.loads(line)
                for line in metrics_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(records[-1]["event"], "session_summary")
            state_path = (
                project_root / ".claude" / "doshitan" / "sessions" / "session-2.json"
            )
            self.assertFalse(state_path.exists())


if __name__ == "__main__":
    unittest.main()
