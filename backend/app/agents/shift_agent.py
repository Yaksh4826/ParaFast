"""Shift Agent — worker node for the Supervisor-Worker graph.

Handles: schedule lookup, populating shifts, submitting shift change requests.
After finishing, sets next_worker = "FINISH" so the supervisor delivers the result.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import ToolNode

logger = logging.getLogger(__name__)

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)

try:
    from backend.app.agents.tools.shift_scraper import _scrape_and_ingest_shifts_sync
    from backend.app.agents.tools.shift_lookup import lookup_shifts
    from backend.app.agents.tools.shift_form_filler import _fill_shift_request_form_sync
except ModuleNotFoundError:
    from app.agents.tools.shift_scraper import _scrape_and_ingest_shifts_sync  # type: ignore
    from app.agents.tools.shift_lookup import lookup_shifts  # type: ignore
    from app.agents.tools.shift_form_filler import _fill_shift_request_form_sync  # type: ignore


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@tool
def populate_shifts(schedule_url: str | None = None) -> dict:
    """Scrape the EAI schedule page and insert all shifts into the database.

    Call this ONCE to populate or refresh shift data.
    Returns inserted_count, skipped_duplicates, and any errors.
    """
    result = _scrape_and_ingest_shifts_sync(schedule_url)
    return {
        "inserted_count": result["inserted_count"],
        "skipped_duplicates": result["skipped_duplicates"],
        "errors": result["errors"],
    }


@tool
def lookup_shift(team_name: str | None = None, shift_date: str | None = None) -> dict:
    """Look up shifts stored in the database.

    Args:
        team_name: e.g. "Team01", "Team25", or just "1".
        shift_date: ISO date string like "2026-03-10". None returns all dates.
    """
    return lookup_shifts(team_name, shift_date)


@tool
def submit_shift_change_request(
    first_name: str,
    last_name: str,
    medic_number: str,
    shift_date: str,
    start_time: str,
    end_time: str,
    requested_action: str,
    reason: str = "",
) -> dict:
    """Submit a Shift Change Request form on the EAI website.

    Args:
        first_name, last_name: Paramedic's name.
        medic_number: e.g. "Team07".
        shift_date: YYYY-MM-DD.
        start_time, end_time: HH:MM:AM/PM or HH:MM 24h.
        requested_action: "Day Off Request" | "Swap Shift" | "Vacation Day" | "Other".
        reason: Required only when requested_action is "Other".
    """
    return _fill_shift_request_form_sync(
        form_url=None,
        first_name=first_name,
        last_name=last_name,
        medic_number=medic_number,
        shift_date=shift_date,
        start_time=start_time,
        end_time=end_time,
        requested_action=requested_action,
        reason=reason or None,
    )


SHIFT_TOOLS = [populate_shifts, lookup_shift, submit_shift_change_request]
shift_tool_node = ToolNode(SHIFT_TOOLS)

_SHIFT_PROMPT = """You are the Shift Agent for EAI Ambulance Service.

Today's date is {today} ({today_weekday}). The current year is {year}.
When the user mentions a month/day without a year, ALWAYS assume {year}.

You have three tools:
1. **populate_shifts** — fetches the schedule from the website and loads it. Call this ONLY when lookup_shift returns empty (no shifts in DB). Do NOT ask the user — do it automatically.
2. **lookup_shift** — queries the DB for shifts by team name and/or date.
3. **submit_shift_change_request** — fills and submits a Shift Change Request form.
   Needs: first_name, last_name, medic_number, shift_date (YYYY-MM-DD),
   start_time (HH:MM:AM/PM), end_time (HH:MM:AM/PM), requested_action.

{user_context}

Rules:
- For schedule questions: call lookup_shift first. If it returns empty (shifts: [] or count: 0), call populate_shifts() immediately, then lookup_shift again. Never ask the user "should I populate?" — they don't know what that means. Just do it. Say "Let me pull that up" or "One sec."
- For shift change requests, use the user's profile for name/medic fields. Submit immediately.
- If required fields are missing, ask for ONLY the missing ones.
- When relaying shift info: speak like a human would. Don't mention team number — the user already knows it.
  Examples: "Yeah, you're on Sunday — 7 to 7 at Station 5, Unit 1122." or "You've got a shift next Sunday, 7 AM to 7 PM at Station 5." Keep it natural and conversational."""


def build_shift_system_message(user_context: str) -> SystemMessage:
    today = date.today()
    return SystemMessage(content=_SHIFT_PROMPT.format(
        today=today.isoformat(),
        today_weekday=today.strftime("%A"),
        year=today.year,
        user_context=user_context,
    ))


def _get_shift_model():
    api_key = os.getenv("OPEN_ROUTER_API_KEY", "")
    return ChatOpenAI(
        model="google/gemini-2.0-flash-001",
        openai_api_key=api_key,
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0,
    ).bind_tools(SHIFT_TOOLS)


_TOOL_CODE_RE = re.compile(r"```tool_code\s*\n(.+?)\n```", re.DOTALL)
_FUNC_CALL_RE = re.compile(r"(\w+)\((.*)?\)", re.DOTALL)
_KW_ARG_RE = re.compile(r"""(\w+)\s*=\s*(?:'([^']*)'|"([^"]*)"|(\S+))""")


def _parse_text_tool_calls(content: str) -> list[dict]:
    """Parse Gemini-style ```tool_code blocks into structured calls."""
    calls = []
    for block in _TOOL_CODE_RE.findall(content):
        m = _FUNC_CALL_RE.match(block.strip())
        if not m:
            continue
        name = m.group(1)
        args_str = m.group(2) or ""
        args = {}
        for am in _KW_ARG_RE.finditer(args_str):
            key = am.group(1)
            val = am.group(2) or am.group(3) or am.group(4) or ""
            args[key] = val
        calls.append({"name": name, "args": args})
    return calls


_ROUTING_TOKENS = ("SHIFT_AGENT", "SCRIBE_AGENT", "PRESHIFT_AGENT", "FINISH")


def _is_supervisor_routing_msg(msg) -> bool:
    """True if this AIMessage is a supervisor routing response (not shift-agent content)."""
    from langchain_core.messages import AIMessage
    if not isinstance(msg, AIMessage):
        return False
    txt = (msg.content or "").strip()
    first_line = txt.split("\n", 1)[0].strip().upper()
    return any(first_line.startswith(t) for t in _ROUTING_TOKENS)


def _build_shift_messages(state: dict) -> list:
    """Build a clean message list for the shift agent (own system prompt, no supervisor noise)."""
    from langchain_core.messages import AIMessage, ToolMessage

    user_ctx = ""
    profile = (state.get("context_data") or {}).get("user_profile")
    if profile:
        user_ctx = (
            f"CURRENT LOGGED-IN USER:\n"
            f"- First name: {profile.get('first_name', 'Unknown')}\n"
            f"- Last name: {profile.get('last_name', 'Unknown')}\n"
            f"- Medic number: {profile.get('medic_number', 'Unknown')}\n"
            f"- Badge number: {profile.get('badge_number', 'Unknown')}\n"
            f"Use this info automatically when submitting shift change requests."
        )
    else:
        user_ctx = "No user profile loaded. Ask for name/medic number if needed."

    shift_sys = build_shift_system_message(user_ctx)
    msgs = [shift_sys]

    for msg in state["messages"]:
        if isinstance(msg, SystemMessage):
            continue
        if _is_supervisor_routing_msg(msg):
            continue
        if isinstance(msg, (AIMessage, ToolMessage, HumanMessage)):
            msgs.append(msg)

    return msgs


def shift_agent_node(state: dict) -> dict:
    """Worker node: invoke the shift LLM with its own clean context."""
    model = _get_shift_model()
    shift_msgs = _build_shift_messages(state)
    response = model.invoke(shift_msgs)

    has_structured = hasattr(response, "tool_calls") and response.tool_calls
    content = response.content or ""

    if not has_structured and "tool_code" in content:
        parsed = _parse_text_tool_calls(content)
        if parsed:
            tool_map = {t.name: t for t in SHIFT_TOOLS}
            results = []
            for call in parsed:
                fn = tool_map.get(call["name"])
                if fn:
                    try:
                        result = fn.invoke(call["args"])
                        results.append(f"{call['name']} returned:\n{json.dumps(result, default=str, indent=2)}")
                    except Exception as exc:
                        results.append(f"{call['name']} error: {exc}")
                else:
                    results.append(f"Unknown tool: {call['name']}")

            result_msg = HumanMessage(
                content="[Tool execution results]\n" + "\n\n".join(results)
            )
            response = model.invoke(shift_msgs + [response, result_msg])

    return {"messages": [response]}


def shift_router(state: dict) -> str:
    """After shift_agent_node: if tool calls pending go to tools, else back to supervisor."""
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "shift_tools"
    return "supervisor"
