"""Draft storage for Para AI. Saves to Supabase form_drafts."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)

try:
    from backend.database import get_supabase_client
except ModuleNotFoundError:
    from database import get_supabase_client  # type: ignore


def get_draft(badge_number: str) -> Optional[Dict[str, Any]]:
    """Load pending draft for user. Returns None if none or already submitted."""
    try:
        sb = get_supabase_client()
        resp = (
            sb.table("form_drafts")
            .select("content, status, updated_at")
            .eq("badge_number", badge_number)
            .limit(1)
            .execute()
        )
        if not resp.data:
            return None
        row = resp.data[0]
        if row.get("status") != "pending":
            return None
        return {"content": row.get("content") or {}, "status": "pending", "updated_at": row.get("updated_at")}
    except Exception:
        return None


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
