"""Pre-Shift Checklist Agent - helps paramedics address BAD status items before shift.

- Silent infilling: medic name, previous shift, BAD items from preshift_checks
- Focus only on what needs fixing. Do not list GOOD items unless asked.
- Route to Scribe when user wants to complete an item (ACRC, report, etc.).
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)

try:
    from backend.app.agents.tools.preshift_data import fetch_preshift_context
except ModuleNotFoundError:
    from app.agents.tools.preshift_data import fetch_preshift_context  # type: ignore

_ROUTING_TOKENS = ("SHIFT_AGENT", "SCRIBE_AGENT", "PRESHIFT_AGENT", "FINISH")


def _is_supervisor_routing_msg(msg) -> bool:
    if not isinstance(msg, AIMessage):
        return False
    txt = (msg.content or "").strip()
    first = txt.split("\n", 1)[0].strip().upper()
    return any(first.startswith(t) for t in _ROUTING_TOKENS)


def _build_preshift_prompt(ctx: Dict[str, Any]) -> str:
    first = ctx.get("first_name", "Unknown")
    bad_items = ctx.get("bad_items", [])
    blocking = ctx.get("blocking_items", [])
    prev_shift = ctx.get("previous_shift")
    acrc_reminder = ctx.get("acrc_reminder", "ACRs must be completed within 24 hours of call completion.")
    cert_reminder = ctx.get("cert_reminder", "Flag if Drivers License not sent or vaccinations not up to date.")

    bad_summary = "\n".join(
        f"- {item.get('check_type', '')}: {item.get('detail', 'needs attention')}"
        for item in bad_items
    ) if bad_items else "(none - all clear)"
    blocking_summary = "; ".join(blocking) if blocking else "none"

    prev_shift_str = ""
    if prev_shift:
        prev_shift_str = f"\nPrevious shift: {prev_shift.get('unit', '')} at {prev_shift.get('station', '')} on {prev_shift.get('date', '')}"

    today = date.today()
    return f"""You are the Pre-Shift Checklist Agent for EAI Ambulance Service.

Today's date is {today.isoformat()} ({today.strftime("%A")}). The current year is {today.year}.

CURRENT LOGGED-IN USER:\n- First name: {first}\n- Last name: {ctx.get('last_name', 'Unknown')}\n- Badge number: {ctx.get('badge_number', 'Unknown')}
{prev_shift_str}

BAD ITEMS (needs attention before shift):
{bad_summary}

BLOCKING ITEMS (Form 4 - must fix before shift): {blocking_summary}

RULES:
- Prioritize BAD items. Inform the medic about outstanding items that need addressing.
- Do NOT list GOOD items unless the user explicitly asks.
- ACRC Detail: Remind that {acrc_reminder}
- Certification Detail: {cert_reminder}
- Be conversational. Morning greeting, then summarize BAD items in a friendly way.
- Offer to help: "Want me to help you finish those now, or should we look at [other item] first?"
- If user wants to complete an item (ACRC, report, occurrence, form), output SCRIBE_AGENT on the next line and a brief handoff like "Got it - passing you to the report team to get that done."
- If user says they're good or ready to go, say something brief and encouraging.
- If there are NO bad items, say "You're all set! Have a good shift." or similar.
- Keep it short. No jargon."""


def _build_preshift_messages(state: dict) -> list:
    from langchain_core.messages import ToolMessage

    badge = state.get("badge_number", "")
    ctx = fetch_preshift_context(badge) if badge else {}
    msgs = [SystemMessage(content=_build_preshift_prompt(ctx))]

    for msg in state["messages"]:
        if isinstance(msg, SystemMessage):
            continue
        if _is_supervisor_routing_msg(msg):
            continue
        if isinstance(msg, (AIMessage, ToolMessage, HumanMessage)):
            msgs.append(msg)

    return msgs


def _get_preshift_model():
    api_key = os.getenv("OPEN_ROUTER_API_KEY", "")
    return ChatOpenAI(
        model="google/gemini-2.0-flash-001",
        openai_api_key=api_key,
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0.3,
    )


def preshift_agent_node(state: dict) -> dict:
    """Worker node: invoke preshift LLM with BAD items and context."""
    model = _get_preshift_model()
    preshift_msgs = _build_preshift_messages(state)
    response = model.invoke(preshift_msgs)
    return {"messages": [response]}


def preshift_router(state: dict) -> str:
    """After preshift_agent: check if we should route to scribe, else back to supervisor."""
    content = (state.get("messages", [])[-1].content or "").strip()
    first_line = content.split("\n", 1)[0].strip().upper()
    if first_line.startswith("SCRIBE_AGENT"):
        return "scribe_agent"
    return "supervisor"
