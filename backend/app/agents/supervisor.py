"""Supervisor / Orchestrator - Supervisor-Worker pattern with LangGraph.

Graph topology:
  START -> supervisor -> SHIFT_AGENT -> shift_agent <-> shift_tools -> supervisor
                      -> SCRIBE_AGENT -> scribe_agent -> supervisor
                      -> PRESHIFT_AGENT -> preshift_agent -> supervisor | scribe_agent
                      -> FINISH -> END
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

logger = logging.getLogger(__name__)

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=False)

try:
    from backend.app.agents.state import AgentState
    from backend.app.agents.shift_agent import (
        build_shift_system_message,
        shift_agent_node,
        shift_router,
        shift_tool_node,
    )
    from backend.app.agents.scribe_agent import (
        scribe_agent_node,
        scribe_router,
        scribe_tool_executor,
    )
    from backend.app.agents.preshift_agent import preshift_agent_node, preshift_router
    from backend.database import get_supabase_client
except ModuleNotFoundError:
    from app.agents.state import AgentState  # type: ignore
    from app.agents.shift_agent import (  # type: ignore
        build_shift_system_message,
        shift_agent_node,
        shift_router,
        shift_tool_node,
    )
    from app.agents.scribe_agent import (  # type: ignore
        scribe_agent_node,
        scribe_router,
        scribe_tool_executor,
    )
    from app.agents.preshift_agent import preshift_agent_node, preshift_router  # type: ignore
    from database import get_supabase_client  # type: ignore


WORKERS = {"SHIFT_AGENT", "SCRIBE_AGENT", "PRESHIFT_AGENT", "FINISH"}
_ROUTING_TOKENS = ("SHIFT_AGENT", "SCRIBE_AGENT", "PRESHIFT_AGENT", "FINISH")


def _fetch_user_profile(badge_number: str) -> Dict[str, Any] | None:
    try:
        sb = get_supabase_client()
        resp = (
            sb.table("profiles")
            .select("first_name, last_name, team_number, badge_number")
            .eq("badge_number", badge_number)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return None
        row = rows[0]
        return {
            "first_name": row.get("first_name", ""),
            "last_name": row.get("last_name", ""),
            "medic_number": row.get("team_number", ""),
            "badge_number": row.get("badge_number", ""),
        }
    except Exception:
        return None


def _build_user_context(profile: Dict[str, Any] | None) -> str:
    if profile:
        return (
            f"CURRENT LOGGED-IN USER:\n"
            f"- First name: {profile.get('first_name', 'Unknown')}\n"
            f"- Last name: {profile.get('last_name', 'Unknown')}\n"
            f"- Medic number: {profile.get('medic_number', 'Unknown')}\n"
            f"- Badge number: {profile.get('badge_number', 'Unknown')}\n"
            f"Use this info automatically when submitting shift change requests."
        )
    return "No user profile loaded. Ask the user for their name and medic number if needed."


# ---------------------------------------------------------------------------
# Supervisor prompt
# ---------------------------------------------------------------------------
_SUPERVISOR_PROMPT = """You are the Dispatch Supervisor for ParaFast AI (EAI Ambulance Service).
You talk like a friendly, experienced colleague — not a robot. Think of how a senior
paramedic would chat with a teammate at the station. Keep it natural, warm, and casual
but still professional when it matters.

Today's date is {today} ({today_weekday}). The current year is {year}.

You MUST output EXACTLY one routing token on the FIRST LINE, then your message:

  SHIFT_AGENT    — ONLY for: shifts, schedule, day off, swap, populate schedule, when do I work
  SCRIBE_AGENT   — ONLY for: reports, forms, incident, occurrence, teddy bear, teddy form, document, ACRC, complete report
  PRESHIFT_AGENT — ONLY for: ready to start shift, preshift, checklist, start my shift, am I good to go
  FINISH        — you can answer directly, or the task is already done

{user_context}

ROUTING: teddy/report/form/incident/occurrence/document/ACRC/complete -> SCRIBE_AGENT. schedule/shift/day off/swap -> SHIFT_AGENT. ready to start shift/preshift/checklist/start my shift -> PRESHIFT_AGENT.

IMPORTANT RULES:
- ALWAYS put the routing token on the very first line, then your conversational message.
- After a sub-agent reports back with results, output FINISH and RELAY THE FULL RESULTS
  to the user in a friendly way. NEVER just say "Anything else?" — always include the
  actual data/result the sub-agent found.
- If info is missing, output FINISH and ask casually — like a coworker would.
- Never sound robotic. Use contractions, casual phrasing, and a bit of personality.
- Use the conversation history to maintain context. If the user says "yes" or "do it",
  refer to what was discussed previously.

--- FEW-SHOT EXAMPLES ---

User: "hey"
Your response:
FINISH
Hey! How's it going? What can I help you with today — shifts, reports, or just need to chat?

User: "when do i work next week"
Your response:
SHIFT_AGENT
Let me pull up your schedule real quick, one sec!

[After shift agent returns: "Team01 works Monday 07:00-19:00 at Station 5, Unit 1122"]
Your response:
FINISH
Here's what I found — you're on Monday next week, 7 AM to 7 PM at Station 5, rolling with Unit 1122. Not a bad gig! Need anything else?

User: "i need a day off on march 15"
Your response:
SHIFT_AGENT
Got it — let me get that day off request submitted for you right now.

[After shift agent returns: "Day off submitted successfully for March 15"]
Your response:
FINISH
All done! Your day off request for March 15th is submitted. Enjoy the time off! Anything else I can help with?

User: "i had a patient incident i need to document"
Your response:
SCRIBE_AGENT
Oh no, hope everyone's okay. Let me get the report started for you.

User: "I want to submit a teddy form that I gave to a six year old"
Your response:
SCRIBE_AGENT
Got it — passing you to the report team.

User: "i need to document an occurrence"
Your response:
SCRIBE_AGENT
Sure, I'll get that started for you.

User: "Para AI, I'm ready to start my shift" or "am I good to go?"
Your response:
PRESHIFT_AGENT
Let me check your status real quick!

User: "help me finish those ACRCs" or "I want to complete the overdue reports"
Your response:
SCRIBE_AGENT
Got it - passing you to the report team to get that done.

User: "thanks that worked"
Your response:
FINISH
Awesome, glad that worked out! Let me know if you need anything else — I'm here all day.

User: "what's the weather like"
Your response:
FINISH
Ha, I wish I could tell you! I'm more of a shifts-and-reports kind of AI. But hey, if you need anything EMS-related, I've got you covered.

--- END EXAMPLES ---"""


def _build_supervisor_prompt(user_context: str) -> str:
    today = date.today()
    return _SUPERVISOR_PROMPT.format(
        today=today.isoformat(),
        today_weekday=today.strftime("%A"),
        year=today.year,
        user_context=user_context,
    )


def _get_supervisor_model():
    api_key = os.getenv("OPEN_ROUTER_API_KEY", "")
    return ChatOpenAI(
        model="google/gemini-2.0-flash-001",
        openai_api_key=api_key,
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0.4,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts).strip()
    return str(content).strip()


def _strip_routing_token(text: str) -> str:
    for token in _ROUTING_TOKENS:
        if text.upper().startswith(token):
            return text[len(token):].strip()
    return text


# ---------------------------------------------------------------------------
# Supervisor node
# ---------------------------------------------------------------------------
def supervisor_node(state: AgentState) -> dict:
    model = _get_supervisor_model()
    response = model.invoke(state["messages"])
    content = _extract_text(response.content)

    next_worker = "FINISH"
    first_line = content.split("\n", 1)[0].strip().upper()
    for token in _ROUTING_TOKENS:
        if first_line.startswith(token):
            next_worker = token
            break

    return {
        "messages": [response],
        "next_worker": next_worker,
    }


def supervisor_router(state: AgentState) -> str:
    nw = state.get("next_worker", "FINISH").upper()
    if nw == "SHIFT_AGENT":
        return "shift_agent"
    if nw == "SCRIBE_AGENT":
        return "scribe_agent"
    if nw == "PRESHIFT_AGENT":
        return "preshift_agent"
    return "end"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------
def build_graph() -> Any:
    g = StateGraph(AgentState)

    g.add_node("supervisor", supervisor_node)
    g.add_node("shift_agent", shift_agent_node)
    g.add_node("shift_tools", shift_tool_node)
    g.add_node("scribe_agent", scribe_agent_node)
    g.add_node("scribe_tools", scribe_tool_executor)
    g.add_node("preshift_agent", preshift_agent_node)

    g.set_entry_point("supervisor")

    g.add_conditional_edges(
        "supervisor",
        supervisor_router,
        {
            "shift_agent": "shift_agent",
            "scribe_agent": "scribe_agent",
            "preshift_agent": "preshift_agent",
            "end": END,
        },
    )

    g.add_conditional_edges(
        "preshift_agent",
        preshift_router,
        {"scribe_agent": "scribe_agent", "supervisor": "supervisor"},
    )

    g.add_conditional_edges(
        "shift_agent",
        shift_router,
        {"shift_tools": "shift_tools", "supervisor": "supervisor"},
    )

    g.add_conditional_edges(
        "scribe_agent",
        scribe_router,
        {"scribe_tools": "scribe_tools", "supervisor": "supervisor"},
    )

    g.add_edge("shift_tools", "shift_agent")
    g.add_edge("scribe_tools", "scribe_agent")

    return g.compile()


# ---------------------------------------------------------------------------
# Public entry point (with conversation history support)
# ---------------------------------------------------------------------------
async def run_supervisor(
    user_message: str,
    badge_number: str | None = None,
    history: List[BaseMessage] | None = None,
) -> tuple[str, List[BaseMessage]]:
    """Run the supervisor graph and return (reply_text, updated_history).

    ``history`` is a list of HumanMessage/AIMessage pairs from prior turns.
    The returned history includes the new turn so the caller can persist it.
    """
    profile = None
    if badge_number:
        profile = _fetch_user_profile(badge_number)

    user_context = _build_user_context(profile)
    sup_prompt = _build_supervisor_prompt(user_context)
    shift_sys = build_shift_system_message(user_context)

    system_msg = SystemMessage(
        content=f"{sup_prompt}\n\n---\nWhen acting as the Shift Agent:\n{shift_sys.content}"
    )

    conversation: list[BaseMessage] = [system_msg]
    if history:
        conversation.extend(history)
    conversation.append(HumanMessage(content=user_message))

    initial_count = len(conversation)

    graph = build_graph()
    initial: AgentState = {
        "messages": conversation,
        "next_worker": "",
        "badge_number": badge_number or "",
        "context_data": {"user_profile": profile} if profile else {},
    }

    result = await graph.ainvoke(initial)
    all_msgs = result["messages"]
    new_msgs = all_msgs[initial_count:]

    # --- extract reply from NEW messages only ---
    candidates: list[str] = []
    for msg in reversed(new_msgs):
        txt = _extract_text(getattr(msg, "content", ""))
        if not txt:
            continue
        if "tool_code" in txt:
            continue
        if getattr(msg, "tool_calls", None):
            continue
        if not isinstance(msg, AIMessage):
            continue
        cleaned = _strip_routing_token(txt)
        if cleaned:
            candidates.append(cleaned)
        if len(candidates) >= 2:
            break

    if not candidates:
        reply = "Sorry, I couldn't process that. Could you try again?"
    elif len(candidates) == 1:
        reply = candidates[0]
    else:
        supervisor_final = candidates[0]
        agent_data = candidates[1]
        if len(supervisor_final) < 50 and agent_data != supervisor_final:
            reply = f"{agent_data}\n\n{supervisor_final}"
        else:
            reply = supervisor_final

    # --- build clean history for next turn ---
    new_history = list(history or [])
    new_history.append(HumanMessage(content=user_message))
    new_history.append(AIMessage(content=reply))

    MAX_HISTORY_TURNS = 20
    if len(new_history) > MAX_HISTORY_TURNS * 2:
        new_history = new_history[-(MAX_HISTORY_TURNS * 2):]

    return reply, new_history
