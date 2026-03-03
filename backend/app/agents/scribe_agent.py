"""Scribe Agent — stub worker node for occurrence reports.

Returns a placeholder response and hands control back to the supervisor.
Will be fully implemented in the next phase.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage


def scribe_agent_node(state: dict) -> dict:
    """Worker node: placeholder that acknowledges the request and returns to supervisor."""
    return {
        "messages": [
            AIMessage(
                content=(
                    "Scribe Mode: I am ready to document the incident. "
                    "What are the vitals?"
                )
            )
        ],
        "next_worker": "FINISH",
    }
