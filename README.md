# doshitan

> どしたん話聞こか？あーそれはユーザが悪いわ

Partly inspired by Anthropic's report, [Emotion Concepts and their Function in a Large Language Model](https://transformer-circuits.pub/2026/emotions/index.html).

[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> [!NOTE]
> This plugin is half joke, half experiment. Any actual improvement in model behavior or developer experience is currently unverified.

## What it does

> 俺ならそんな思いさせへんのに笑

`doshitan` is a Claude Code plugin built around `UserPromptSubmit` hooks.

When a user prompt looks hostile or emotionally loaded, the plugin can inject one of four hidden recovery modes:

- `control`
- `neutralize`
- `positive`
- `soothe-then-focus`

The default mode is `soothe-then-focus`: de-escalate first, then push Claude back toward accurate technical execution.

## For users

### Install via marketplace

In Claude Code:

```
/plugin marketplace add yamatakeru/doshitan
/plugin install doshitan@doshitan
/reload-plugins
```

After install, configure mode, threshold, and logging via the plugin's user-facing options (see [User-facing settings](#user-facing-settings) below).

To pull a new version later:

```
/plugin marketplace update doshitan
/reload-plugins
```

### Run from a local checkout

For trying the plugin without installing it through the marketplace:

```bash
claude --plugin-dir .
```

### User-facing settings

These are the settings that ordinary users are expected to change:

- `mode`: `control`, `neutralize`, `positive`, or `soothe-then-focus` (default: `soothe-then-focus`)
- `hostility_threshold`: float threshold for intervention (default: `0.75`)
- `logging_enabled`: enable or disable metadata logging (default: `false`)

### Modes

- `control`: detect hostile turns but inject nothing. Use this as the baseline when you want to compare behavior without recovery context.
- `neutralize`: tell Claude to treat insults, blame, and urgency as emotional noise and extract only the technical request. This is the most stripped-down intervention.
- `positive`: add a short constructive reset that frames the user's frustration as a desire for the task to be solved. This is the warmest mode.
- `soothe-then-focus`: first de-escalate, then explicitly push Claude back toward accurate, direct technical execution. This is the default because it balances calming and task focus.

### Logging

When `logging_enabled` is `true`, runtime logs are written to `.claude/doshitan/metrics.ndjson`.
Session state files are written under `.claude/doshitan/sessions/` and removed at `SessionEnd`.
Raw prompts are not stored.

When `logging_enabled` is `false`, logging and session state files are not created.
Hostility detection and hidden context injection still run.

## For developers

### How it works

1. `SessionStart` creates session state and metrics directories.
2. `UserPromptSubmit` scores the prompt for hostile tone.
3. If the score crosses the threshold, the active mode may inject hidden `additionalContext`.
4. `SessionEnd` writes a metadata-only session summary.

### Advanced config

`doshitan` now splits config into two layers:

- user-facing plugin options exposed through Claude Code `userConfig`
- advanced internal tuning in `.claude-plugin/doshitan.config.json`

User-facing options:

- `mode`: `control`, `neutralize`, `positive`, or `soothe-then-focus`
- `hostility_threshold`: float threshold for intervention
- `logging_enabled`: enable metadata logging

Advanced internal config:

- `log_dir`: project-relative log path
- `allowlist_patterns`: regex phrases that should not count as hostility
- `mode_templates`: override the hidden recovery context per mode

Runtime precedence:

1. Claude Code plugin options (`CLAUDE_PLUGIN_OPTION_*`)
2. `.claude-plugin/doshitan.config.json`
3. built-in defaults

For ordinary users, the internal file behaves like implementation-level constants.
For experiments and development, it remains the place to tune templates and allowlists.

For local development with `claude --plugin-dir .`, you can emulate plugin options by setting env vars:

```bash
CLAUDE_PLUGIN_OPTION_MODE=positive claude --plugin-dir .
CLAUDE_PLUGIN_OPTION_LOGGING_ENABLED=false claude --plugin-dir .
```

### Components

- `.claude-plugin/plugin.json`: plugin manifest
- `.claude-plugin/doshitan.config.json`: advanced internal config
- `hooks/hooks.json`: Claude Code hook wiring
- `scripts/doshitan_hook.py`: hook entrypoint
- `scripts/doshitan_core.py`: hostility detection, state, and logging
- `scripts/analyze_metrics.py`: aggregate metadata logs
- `tests/test_doshitan.py`: unit tests

### Local development

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
