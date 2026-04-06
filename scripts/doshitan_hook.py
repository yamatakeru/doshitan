#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from typing import cast

from doshitan_core import HookPayload, dispatch_hook


def _load_payload() -> HookPayload:
    loaded = cast(object, json.load(sys.stdin))
    if not isinstance(loaded, dict):
        return {}

    payload: HookPayload = {}
    raw_payload = cast(dict[object, object], loaded)
    for key, value in raw_payload.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if key == "session_id":
            payload["session_id"] = value
        elif key == "hook_event_name":
            payload["hook_event_name"] = value
        elif key == "cwd":
            payload["cwd"] = value
        elif key == "prompt":
            payload["prompt"] = value
        elif key == "source":
            payload["source"] = value
        elif key == "model":
            payload["model"] = value
        elif key == "agent_type":
            payload["agent_type"] = value
        elif key == "reason":
            payload["reason"] = value
    return payload


def main() -> int:
    payload = _load_payload()
    result = dispatch_hook(payload)
    if result is not None:
        json.dump(result, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
