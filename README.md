# Doshitan

> どしたん話聞こか？あーそれは彼氏が悪いわ

Partly inspired by Anthropic's report, [Emotion Concepts and their Function in a Large Language Model](https://transformer-circuits.pub/2026/emotions/index.html).

[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## What it does

> 俺ならそんな思いさせへんのに笑

`doshitan` is a Claude Code plugin built around `UserPromptSubmit` hooks.

When a user prompt looks hostile or emotionally loaded, the plugin can inject one of four hidden recovery modes:

- `control`
- `neutralize`
- `positive`
- `soothe-then-focus`

The default mode is `soothe-then-focus`: de-escalate first, then push Claude back toward accurate technical execution.

## Components

- `.claude-plugin/plugin.json`: plugin manifest
- `.claude-plugin/doshitan.config.json`: experiment config
- `hooks/hooks.json`: Claude Code hook wiring
- `scripts/doshitan_hook.py`: hook entrypoint
- `scripts/doshitan_core.py`: hostility detection, state, and logging
- `scripts/analyze_metrics.py`: aggregate metadata logs
- `tests/test_doshitan.py`: unit tests

## How it works

1. `SessionStart` creates session state and metrics directories.
2. `UserPromptSubmit` scores the prompt for hostile tone.
3. If the score crosses the threshold, the active mode may inject hidden `additionalContext`.
4. `SessionEnd` writes a metadata-only session summary.

Runtime logs are written to `.claude/doshitan/metrics.ndjson`.
Raw prompts are not stored.

## Config

Edit `.claude-plugin/doshitan.config.json`.

Key fields:

- `mode`: `control`, `neutralize`, `positive`, or `soothe-then-focus`
- `hostility_threshold`: float threshold for intervention
- `logging_enabled`: enable metadata logging
- `log_dir`: project-relative log path
- `allowlist_patterns`: regex phrases that should not count as hostility
- `mode_templates`: override the hidden recovery context per mode

## Local development

Run the plugin directly from this repository:

```bash
claude --plugin-dir .
```

Useful commands:

```bash
python3 -m unittest -v tests/test_doshitan.py
python3 scripts/analyze_metrics.py
claude plugin validate .
```

## Why this exists

The motivating hypothesis is simple:

- hostile user prompts may degrade Claude Code's behavior
- hidden recovery context may improve developer experience
- different intervention styles should be compared, not assumed

This repository is for that experiment.
