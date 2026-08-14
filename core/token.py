from __future__ import annotations

import json
from pathlib import Path

from core.config import auth_state_path


def load_auth_token(source: str = "acegrade") -> str | None:
    path = auth_state_path(source)
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    for origin in state.get("origins", []):
        for item in origin.get("localStorage", []):
            if item.get("name") == "auth_token" and item.get("value"):
                return str(item["value"])
    return None
