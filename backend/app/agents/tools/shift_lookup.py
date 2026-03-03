import re
from typing import Any, Dict

try:
    from backend.database import get_supabase_client
except ModuleNotFoundError:
    from database import get_supabase_client  # type: ignore


def lookup_shifts(
    team_name: str | None = None,
    shift_date: str | None = None,
) -> Dict[str, Any]:
    """Query the Supabase shifts table for a team and/or date.

    Args:
        team_name: e.g. "Team01" or "1" — normalized to "Team01" format.
        shift_date: ISO date like "2026-03-10", or None for all dates.

    Returns:
        {"shifts": [...], "count": int}
    """
    supabase = get_supabase_client()
    query = supabase.table("shifts").select("*")

    if team_name:
        digits = re.sub(r"\D", "", team_name)
        if digits:
            normalized = f"Team{int(digits):02d}"
            query = query.eq("team_number", normalized)

    if shift_date:
        query = query.eq("shift_date", shift_date)

    query = query.order("shift_date")
    resp = query.execute()
    rows = resp.data or []

    return {"shifts": rows, "count": len(rows)}
