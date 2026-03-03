"""Shared AgentState for the entire Supervisor-Worker graph.

Every node reads and writes to this single state object.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Dict, List

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict


class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    next_worker: str
    badge_number: str
    context_data: Dict[str, Any]
