"""The User-Agent for Wikimedia sites (Wikidata, Wikipedia). Their policy asks automated clients to name themselves and give a contact; they
answer 403 to a browser's User-Agent from a program. Every other site keeps the browser one (publishers treat a named program worse)."""
from __future__ import annotations

import os

DEFAULT = "NewsAnchor/1.0 (personal news reader; set NA_WIKIMEDIA_USER_AGENT to add a contact address)"


def user_agent() -> str:
    return os.environ.get("NA_WIKIMEDIA_USER_AGENT", "").strip() or DEFAULT
