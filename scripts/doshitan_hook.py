#!/usr/bin/env python3

from __future__ import annotations

import json
import sys

from doshitan_core import dispatch_hook


def main() -> int:
    payload = json.load(sys.stdin)
    result = dispatch_hook(payload)
    if result is not None:
        json.dump(result, sys.stdout, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
