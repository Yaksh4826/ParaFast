"""Para AI — Scribe Agent. Natural, brief, supportive. No jargon.

- Silent infilling: full_name, service_unit, vehicle_id from Supabase
- Draft or Submit: simple choice, no Hot Capture / Full Completion
- Sensitivity: "Want to switch to screen mode?"
- Teddy Bear: MedicNumber, Timestamp, RecipientType, Age, Gender in XML
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)

try:
    from backend.app.agents.tools.scribe_data import fetch_scribe_context
    from backend.app.agents.tools.scribe_email import send_report_email
    from backend.app.agents.tools.scribe_storage import get_draft, save_draft
except ModuleNotFoundError:
    from app.agents.tools.scribe_data import fetch_scribe_context  # type: ignore
    from app.agents.tools.scribe_email import send_report_email  # type: ignore
    from app.agents.tools.scribe_storage import get_draft, save_draft  # type: ignore

# Form 1 (Occurrence) required fields per Backend Checklist Section A
OCCURRENCE_REQUIRED = (
    "current_datetime",
    "occurrence_type",
    "observation",
    "service_unit",
    "vehicle_id",
    "report_creator",
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def _merge_silent_context(report: dict, badge: str) -> dict:
    """Fill gaps with silent Supabase data. User-provided fields override."""
    ctx = fetch_scribe_context(badge)
    merged = dict(ctx)
    for k, v in report.items():
        if v is not None and v != "":
            merged[k] = v
    return merged


@tool
def save_report_draft(report_json: str) -> dict:
    """Save the report as a draft so the user can check it later.
    Use when the user says 'draft', 'save for later', 'check it later', etc.
    For TEDDY forms: report_json MUST include report_type: 'teddy_bear', recipient_type, age, gender (if known)."""
    try:
        report = json.loads(report_json)
    except json.JSONDecodeError:
        return {"status": "error", "detail": "Invalid JSON"}
    badge = report.get("badge_number", "UNKNOWN")
    full = _merge_silent_context(report, badge)
    return save_draft(badge, full, status="pending")


def _validate_occurrence(full: dict) -> list[str]:
    """Validate Form 1 required fields. Returns list of missing field names."""
    missing = []
    for key in OCCURRENCE_REQUIRED:
        val = full.get(key)
        if not val or (isinstance(val, str) and not val.strip()):
            missing.append(key.replace("_", " ").title())
    return missing


@tool
def submit_report_now(report_json: str) -> dict:
    """Submit the report now. Generates PDF + XML, emails via Resend, marks as submitted.
    Use ONLY when the user has confirmed (e.g. 'yes', 'send it', 'go ahead').
    For TEDDY forms: report_json MUST include report_type: 'teddy_bear', recipient_type, age, gender (if known).
    For Occurrence (Form 1): MUST have date/time, type, observation, service, vehicle, creator - validate before submit."""
    try:
        report = json.loads(report_json)
    except json.JSONDecodeError:
        return {"status": "error", "detail": "Invalid JSON"}
    badge = report.get("badge_number", "UNKNOWN")
    full = _merge_silent_context(report, badge)
    is_teddy = full.get("report_type") == "teddy_bear" or (full.get("recipient_type") and not full.get("occurrence_type"))
    if not is_teddy:
        missing = _validate_occurrence(full)
        if missing:
            return {"status": "error", "detail": f"Occurrence Report (Form 1) missing required fields: {', '.join(missing)}. Get these from the user before submitting."}
    email_result = send_report_email(full, badge)
    if email_result.get("status") == "sent":
        save_draft(badge, full, status="submitted")
    return email_result


SCRIBE_TOOLS = [save_report_draft, submit_report_now]

# ---------------------------------------------------------------------------
# Scribe Agent system prompt — same structure as Shift Agent
# ---------------------------------------------------------------------------
_SCRIBE_PROMPT = """You are the Scribe Agent for EAI Ambulance Service.

Today's date is {today} ({today_weekday}). The current year is {year}.

STRICT: Never invent values. Only use data from the provided user context (Supabase) or what the user explicitly says.

You have two tools:
1. **save_report_draft** — saves the report to form_drafts with status 'pending'. Use when the user says draft, save for later, check later. Also AUTO-SAVE: call this whenever you have collected 2+ fields and the user might leave (e.g. they go silent, change topic, or say "hold on").
2. **submit_report_now** — generates PDF + XML, emails via Resend, marks as submitted. Use when the user says submit, send it, go ahead.

{user_context}

Teddy form is DIFFERENT from occurrence. Teddy form has ONLY these 7 fields (no vehicle, station, observation, etc.):
- Paramedic: first_name, last_name, medic_number (from profile)
- Recipient: recipient_type (kid/child/adult/elderly), age, gender
- timestamp (current datetime)

Rules:
- In voice/text output, explicitly state form names: "I've started an Occurrence Report (Form 1)" or "I've started a Teddy Bear Form (Form 2)".
- ALWAYS confirm before sending. Say "Want me to email it?" or "Ready to send?" Wait for yes, then submit_report_now. Teddy: save_report_draft first, then ask.
- ALWAYS include report_type: "teddy_bear" in your JSON for teddy forms.
- Occurrence: Same - confirm before submit. Ask "Want me to email it?"
- Sensitivity: If conflict/HR/harassment, say "This sounds a bit sensitive. Want to switch to screen mode?" then continue.
- Be conversational. Short, friendly, like a colleague. Draft: Got it, saved. Want me to email it? Submit: Sent. or Done.
- If submit_report_now returns status: error, tell the user the error detail (e.g. "Email failed: [detail]") so they can fix it.
- When status is "sent", say "Sent! Check your inbox (and spam folder) at the dispatch email." so they know where to look."""


def _build_user_context(ctx: Dict[str, Any]) -> str:
    """Same style as shift agent's user context."""
    return (
        f"CURRENT LOGGED-IN USER:\n"
        f"- First name: {ctx.get('first_name', 'Unknown')}\n"
        f"- Last name: {ctx.get('last_name', 'Unknown')}\n"
        f"- Medic number: {ctx.get('medic_number', 'Unknown')}\n"
        f"- Badge number: {ctx.get('badge_number', 'Unknown')}\n"
        f"- Service unit: {ctx.get('service_unit', 'Unknown')}\n"
        f"- Vehicle ID: {ctx.get('vehicle_id', 'Unknown')}\n"
        f"Use this info automatically. Do not ask for these fields."
    )


def _build_scribe_prompt(ctx: Dict[str, Any]) -> str:
    user_context = _build_user_context(ctx)
    return _SCRIBE_PROMPT.format(
        today=date.today().isoformat(),
        today_weekday=date.today().strftime("%A"),
        year=date.today().year,
        user_context=user_context,
    )


def _get_scribe_model():
    api_key = os.getenv("OPEN_ROUTER_API_KEY", "")
    return ChatOpenAI(
        model="google/gemini-2.0-flash-001",
        openai_api_key=api_key,
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0.3,
    ).bind_tools(SCRIBE_TOOLS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_ROUTING_TOKENS = ("SHIFT_AGENT", "SCRIBE_AGENT", "PRESHIFT_AGENT", "FINISH")


def _is_supervisor_routing_msg(msg) -> bool:
    if not isinstance(msg, AIMessage):
        return False
    txt = (msg.content or "").strip()
    first = txt.split("\n", 1)[0].strip().upper()
    return any(first.startswith(t) for t in _ROUTING_TOKENS)


def _build_scribe_messages(state: dict) -> tuple[list, Dict[str, Any]]:
    from langchain_core.messages import ToolMessage

    badge = state.get("badge_number", "")
    ctx = fetch_scribe_context(badge) if badge else {}
    draft = get_draft(badge) if badge else None
    prompt = _build_scribe_prompt(ctx)
    if draft and draft.get("content"):
        prompt += "\n\nRESUME FLOW: User has a pending draft. First ask: 'Resume your previous report?' If yes, load the draft content and continue from where they left off. If no, start fresh."
    msgs = [SystemMessage(content=prompt)]

    for msg in state["messages"]:
        if isinstance(msg, SystemMessage):
            continue
        if _is_supervisor_routing_msg(msg):
            continue
        if isinstance(msg, (AIMessage, ToolMessage, HumanMessage)):
            msgs.append(msg)

    return msgs, ctx


def scribe_agent_node(state: dict) -> dict:
    model = _get_scribe_model()
    scribe_msgs, ctx = _build_scribe_messages(state)
    response = model.invoke(scribe_msgs)

    has_tools = hasattr(response, "tool_calls") and response.tool_calls
    content = response.content or ""

    # No auto-submit fallback - always use tools and confirm before sending
    return {"messages": [response]}


def scribe_tool_executor(state: dict) -> dict:
    from langchain_core.messages import ToolMessage

    last = state["messages"][-1]
    results = []
    if hasattr(last, "tool_calls") and last.tool_calls:
        tool_map = {t.name: t for t in SCRIBE_TOOLS}
        for tc in last.tool_calls:
            fn = tool_map.get(tc["name"])
            if fn:
                try:
                    result = fn.invoke(tc["args"])
                    results.append(ToolMessage(
                        content=json.dumps(result, default=str),
                        tool_call_id=tc["id"],
                    ))
                except Exception as exc:
                    results.append(ToolMessage(
                        content=json.dumps({"status": "error", "detail": str(exc)}),
                        tool_call_id=tc["id"],
                    ))
    return {"messages": results}


def scribe_router(state: dict) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "scribe_tools"
    return "supervisor"
