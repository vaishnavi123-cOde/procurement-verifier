"""Agent framework: registry + observability wrapper.

Every pipeline step is a named agent with a deterministic core. The wrapper
persists one ``AgentExecution`` row per run and records tool calls, so each
recommendation is fully auditable (which agent said what, from which evidence).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session

from backend.app.repositories import store

AgentFn = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    fn: AgentFn


_REGISTRY: dict[str, AgentSpec] = {}


def register_agent(name: str, description: str) -> Callable[[AgentFn], AgentFn]:
    def decorator(fn: AgentFn) -> AgentFn:
        _REGISTRY[name] = AgentSpec(name=name, description=description, fn=fn)
        return fn
    return decorator


def get_agent(name: str) -> AgentSpec | None:
    return _REGISTRY.get(name)


def list_agents() -> list[AgentSpec]:
    return list(_REGISTRY.values())


def run_agent(db: Session, *, name: str, case_id: str, request_id: str, run_id: str,
              task: dict[str, Any]) -> dict[str, Any]:
    """Execute a registered agent and record its execution/outcome."""
    spec = get_agent(name)
    if spec is None:
        raise KeyError(f"Agent '{name}' is not registered.")
    start = time.monotonic()
    rec = store.add_agent_execution(
        db, case_id=case_id, request_id=request_id, agent=name, run_id=run_id,
        status="running", task=task,
    )
    db.commit()
    try:
        output = spec.fn(**task)
        status = "ok"
    except Exception as exc:  # noqa: BLE001 - agent failures are recorded, not raised
        output = {}
        status = "error"
        error = str(exc)
    else:
        error = None
    duration_ms = int((time.monotonic() - start) * 1000)
    store.finish_agent_execution(db, rec.id, status=status, output=output,
                                 error=error, duration_ms=duration_ms)
    db.commit()
    return {"agent": name, "status": status, "output": output,
            "error": error, "duration_ms": duration_ms}


def tool(db: Session, *, case_id: str, request_id: str, agent: str, tool_name: str,
         inputs: dict | None = None, outputs: dict | None = None) -> None:
    """Record a tool call for the audit trail."""
    start = time.monotonic()
    store.add_tool_call(db, case_id=case_id, request_id=request_id, agent=agent,
                        tool_name=tool_name, inputs=inputs or {}, outputs=outputs or {},
                        duration_ms=int((time.monotonic() - start) * 1000))
    db.commit()


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]