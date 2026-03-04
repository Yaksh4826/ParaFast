"""Silent data retrieval for Para AI (Scribe Agent).

Fetches profile + active shift from Supabase. Used to auto-fill
full_name, service_unit, vehicle_id without asking the paramedic.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)

try:
    from backend.database import get_supabase_client
except ModuleNotFoundError:
    from database import get_supabase_client  # type: ignore


def fetch_scribe_context(badge_number: str) -> Dict[str, Any]:
    """Silently fetch context for report auto-fill. Do not list to user.

    Returns: full_name, service_unit, vehicle_id, current_datetime,
    report_creator, badge_number, station_name, team_number, shift_date, etc.
    """
    sb = get_supabase_client()
    ctx: Dict[str, Any] = {
        "badge_number": badge_number,
        "current_datetime": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "report_creator": "Unknown",
        "full_name": "Unknown",
        "first_name": "Unknown",
        "last_name": "Unknown",
        "medic_number": "Unknown",
        "service_unit": "EAI Ambulance Service",
        "vehicle_id": "Unknown",
        "team_number": "Unknown",
        "station_name": "Unknown",
        "shift_date": "",
        "shift_start": "",
        "shift_end": "",
    }

    try:
        profile_resp = (
            sb.table("profiles")
            .select("first_name, last_name, team_number, badge_number")
            .eq("badge_number", badge_number)
            .limit(1)
            .execute()
        )
        rows = profile_resp.data or []
        if rows:
            p = rows[0]
            first = p.get("first_name", "")
            last = p.get("last_name", "")
            full = f"{first} {last}".strip() or "Unknown"
            medic = p.get("team_number", "Unknown")
            ctx["report_creator"] = full
            ctx["full_name"] = full
            ctx["first_name"] = first
            ctx["last_name"] = last
            ctx["medic_number"] = medic
            ctx["team_number"] = medic
            ctx["service_unit"] = medic
    except Exception:
        pass

    today_iso = date.today().isoformat()
    team = ctx["team_number"]

    try:
        shift_resp = (
            sb.table("shifts")
            .select("unit_name, station_name, shift_date, start_time, end_time")
            .eq("team_number", team)
            .eq("shift_date", today_iso)
            .limit(1)
            .execute()
        )
        shift_rows = shift_resp.data or []
        if shift_rows:
            s = shift_rows[0]
            ctx["vehicle_id"] = s.get("unit_name", "Unknown")
            ctx["station_name"] = s.get("station_name", "Unknown")
            ctx["shift_date"] = s.get("shift_date", today_iso)
            ctx["shift_start"] = s.get("start_time", "")
            ctx["shift_end"] = s.get("end_time", "")
        else:
            ctx["shift_date"] = today_iso
    except Exception:
        ctx["shift_date"] = today_iso

    return ctx
