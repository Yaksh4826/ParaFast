import asyncio
import os
import re
import urllib.request
from datetime import datetime
from typing import Any, Dict, List

try:
    from backend.database import get_supabase_client
except ModuleNotFoundError:
    from database import get_supabase_client  # type: ignore

_WEEKDAY_RE = re.compile(
    r"^(Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)$", re.I
)
_DATE_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}$", re.I
)
_TIME_RANGE_RE = re.compile(
    r"(\d{1,2}:\d{2}\s*[AP]M)\s*[–\-]\s*(\d{1,2}:\d{2}\s*[AP]M)", re.I
)
_TEAM_LINE_RE = re.compile(r"Team\s*\d+", re.I)
_TEAM_NUM_RE = re.compile(r"Team\s*0*(\d+)", re.I)
_UNIT_RE = re.compile(r"^Unit\s+\S+", re.I)


def _parse_date(raw: str, year: int) -> str:
    raw = raw.strip().rstrip(",")
    for fmt in ("%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(f"{raw} {year}", fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Unrecognized date: {raw}")


def _parse_time(raw: str) -> str:
    raw = raw.strip()
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            return datetime.strptime(raw.upper(), fmt).strftime("%H:%M")
        except ValueError:
            continue
    raise ValueError(f"Unrecognized time: {raw}")


def _matches_team(team_numbers: List[int], team_keyword: str | None) -> bool:
    if not team_keyword:
        return True
    digits = re.sub(r"\D", "", team_keyword)
    if not digits:
        return True
    target = str(int(digits))
    return any(str(n) == target for n in team_numbers)


def _fetch_page_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S | re.I)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</div>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core parser: treat DOM as plain text, walk lines with a state machine.
# State: current_day, current_date, current_station, current_unit,
#        current_start, current_end
# Station and unit are "sticky" — inherited forward until replaced.
# ---------------------------------------------------------------------------
def parse_shifts_from_text(
    text: str, year: int, team_keyword: str | None = None
) -> List[Dict[str, Any]]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    shifts: List[Dict[str, Any]] = []

    current_day = ""
    current_date = ""
    current_station = ""
    current_unit = ""
    current_start = ""
    current_end = ""

    i = 0
    while i < len(lines):
        ln = lines[i]

        if _WEEKDAY_RE.match(ln):
            current_day = ln.title()
            current_station = ""
            current_unit = ""
            current_start = ""
            current_end = ""
            if i + 1 < len(lines) and _DATE_RE.match(lines[i + 1]):
                try:
                    current_date = _parse_date(lines[i + 1], year)
                except Exception:
                    current_date = ""
                i += 2
                continue
            i += 1
            continue

        if _DATE_RE.match(ln):
            try:
                current_date = _parse_date(ln, year)
            except Exception:
                pass
            i += 1
            continue

        m_time = _TIME_RANGE_RE.search(ln)
        if m_time:
            try:
                current_start = _parse_time(m_time.group(1))
                current_end = _parse_time(m_time.group(2))
            except Exception:
                current_start = current_end = ""
            i += 1
            continue

        teams = [int(n) for n in _TEAM_NUM_RE.findall(ln)]
        if teams and current_date and current_start and current_end:
            if _matches_team(teams, team_keyword):
                shifts.append({
                    "shift_date": current_date,
                    "shift_day": current_day,
                    "start_time": current_start,
                    "end_time": current_end,
                    "station_name": current_station,
                    "unit_name": current_unit,
                    "team_numbers": sorted(set(teams)),
                    "teams_text": ln,
                })
            i += 1
            continue

        if _UNIT_RE.match(ln):
            current_unit = ln
        elif not _TEAM_LINE_RE.search(ln) and not _TIME_RANGE_RE.search(ln):
            stripped = ln.strip()
            if stripped and len(stripped) < 40:
                current_station = stripped

        i += 1

    return shifts


# ---------------------------------------------------------------------------
# GET route handler: fetch HTML as text, parse, return shifts (no DB write)
# ---------------------------------------------------------------------------
def _fetch_dom_and_extract_sync(
    schedule_url: str | None = None,
    team_keyword: str | None = None,
    include_html: bool = False,
) -> Dict[str, Any]:
    url = schedule_url or os.getenv("SHIFT_SCHEDULE_URL")
    if not url:
        return {"success": False, "errors": ["Missing schedule URL."], "dom_text": "", "shifts": []}

    m_year = re.search(r"(20\d{2})", url)
    year = int(m_year.group(1)) if m_year else datetime.now().year

    errors: List[str] = []
    shifts: List[Dict[str, Any]] = []
    dom_text = ""

    try:
        dom_text = _fetch_page_text(url)
        shifts = parse_shifts_from_text(dom_text, year, team_keyword)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Fetch/parse error: {exc}")

    return {
        "success": len(shifts) > 0,
        "shift_count": len(shifts),
        "errors": errors if errors else ([] if shifts else ["No records found."]),
        "dom_text": dom_text[:20000],
        "shifts": shifts,
    }


async def fetch_dom_and_extract(
    schedule_url: str | None = None,
    team_keyword: str | None = None,
    include_html: bool = False,
) -> Dict[str, Any]:
    return await asyncio.to_thread(_fetch_dom_and_extract_sync, schedule_url, team_keyword, include_html)


# ---------------------------------------------------------------------------
# POST route handler: fetch, parse, upsert to Supabase
# ---------------------------------------------------------------------------
def _scrape_and_ingest_shifts_sync(
    schedule_url: str | None = None, team_keyword: str | None = None
) -> Dict[str, Any]:
    url = schedule_url or os.getenv("SHIFT_SCHEDULE_URL")
    if not url:
        return {"inserted_count": 0, "skipped_duplicates": 0, "errors": ["Missing schedule URL."], "shifts": []}

    m_year = re.search(r"(20\d{2})", url)
    year = int(m_year.group(1)) if m_year else datetime.now().year

    errors: List[str] = []
    shifts: List[Dict[str, Any]] = []

    try:
        dom_text = _fetch_page_text(url)
        shifts = parse_shifts_from_text(dom_text, year, team_keyword)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Fetch/parse error: {exc}")

    if not shifts:
        return {"inserted_count": 0, "skipped_duplicates": 0, "errors": errors or ["No records found."], "shifts": []}

    db_rows: List[Dict[str, Any]] = []
    for s in shifts:
        for t in s["team_numbers"]:
            db_rows.append({
                "shift_date": s["shift_date"],
                "shift_day": s["shift_day"],
                "start_time": s["start_time"],
                "end_time": s["end_time"],
                "team_number": f"Team{t:02d}",
                "medic1_name": "",
                "medic2_name": "",
                "unit_name": s["unit_name"],
                "station_name": s["station_name"],
            })

    supabase = get_supabase_client()
    unique_dates = list({r["shift_date"] for r in db_rows})
    existing_resp = (
        supabase.table("shifts")
        .select("shift_date,team_number")
        .in_("shift_date", unique_dates)
        .execute()
    )
    existing = {(row["shift_date"], row["team_number"]) for row in (existing_resp.data or [])}
    to_insert = [r for r in db_rows if (r["shift_date"], r["team_number"]) not in existing]

    inserted_count = 0
    if to_insert:
        resp = supabase.table("shifts").upsert(to_insert, on_conflict="shift_date,team_number").execute()
        inserted_count = len(resp.data or to_insert)

    return {
        "inserted_count": inserted_count,
        "skipped_duplicates": len(db_rows) - inserted_count,
        "errors": errors,
        "shifts": shifts,
    }


async def scrape_and_ingest_shifts(
    schedule_url: str | None = None, team_keyword: str | None = None
) -> Dict[str, Any]:
    return await asyncio.to_thread(_scrape_and_ingest_shifts_sync, schedule_url, team_keyword)
