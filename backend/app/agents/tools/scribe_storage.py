"""Draft storage for Para AI. Saves to Supabase form_drafts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)

try:
    from backend.database import get_supabase_client
except ModuleNotFoundError:
    from database import get_supabase_client  # type: ignore


def save_draft(badge_number: str, content: Dict[str, Any], status: str = "pending") -> Dict[str, Any]:
    """Save report to form_drafts. status: 'pending' (draft) or 'submitted'."""
    try:
        sb = get_supabase_client()
        sb.table("form_drafts").upsert(
            {
                "badge_number": badge_number,
                "content": content,
                "status": status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="badge_number",
        ).execute()
        return {"status": "ok", "saved_as": status}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}
