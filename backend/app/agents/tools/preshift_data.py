"""Silent data retrieval for Para AI (Pre-Shift Checklist Agent).

Fetches preshift_checks (BAD items) and profile + previous shift from Supabase.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)

try:
    from backend.database import get_supabase_client
except ModuleNotFoundError:
    from database import get_supabase_client  # type: ignore


def fetch_preshift_context(badge_number: str) -> Dict[str, Any]:
    """Fetch BAD preshift items, medic name, and previous shift. Silent infilling.

    Returns:
        first_name, last_name, badge_number, bad_items (list of {check_type, status, detail}),
        previous_shift (unit, station, date), acrc_reminder, cert_reminder.
    """
    sb = get_supabase_client()
    ctx: Dict[str, Any] = {
        "first_name": "Unknown",
        "last_name": "Unknown",
        "badge_number": badge_number,
        "bad_items": [],
        "previous_shift": None,
        "acrc_reminder": "ACRs must be completed within 24 hours of call completion.",
        "cert_reminder": "Flag if Drivers License image not sent or vaccinations not up to date.",
    }

    try:
        profile_resp = (
            sb.table("profiles")
            .select("first_name, last_name, team_number")
            .eq("badge_number", badge_number)
            .limit(1)
            .execute()
        )
        if profile_resp.data:
            p = profile_resp.data[0]
            ctx["first_name"] = p.get("first_name", "Unknown")
            ctx["last_name"] = p.get("last_name", "Unknown")
    except Exception:
        pass

    try:
        checks_resp = (
            sb.table("preshift_checks")
            .select("check_type, status, detail")
            .eq("badge_number", badge_number)
            .eq("status", "BAD")
            .execute()
        )
        if checks_resp.data:
            ctx["bad_items"] = [
                {
                    "check_type": r.get("check_type", ""),
                    "status": r.get("status", "BAD"),
                    "detail": r.get("detail", ""),
                }
                for r in checks_resp.data
            ]
    except Exception:
        ctx["bad_items"] = []

    today_iso = date.today().isoformat()
    try:
        profile_resp2 = (
            sb.table("profiles")
            .select("team_number")
            .eq("badge_number", badge_number)
            .limit(1)
            .execute()
        )
        team = (profile_resp2.data or [{}])[0].get("team_number", "")
        if team:
            shift_resp = (
                sb.table("shifts")
                .select("unit_name, station_name, shift_date")
                .eq("team_number", team)
                .lte("shift_date", today_iso)
                .order("shift_date", desc=True)
                .limit(1)
                .execute()
            )
            if shift_resp.data:
                s = shift_resp.data[0]
                ctx["previous_shift"] = {
                    "unit": s.get("unit_name", ""),
                    "station": s.get("station_name", ""),
                    "date": s.get("shift_date", ""),
                }
    except Exception:
        pass

    return ctx
