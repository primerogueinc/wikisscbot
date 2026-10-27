from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def settings() -> dict:
    return json.loads((ROOT / "config" / "settings.json").read_text())
