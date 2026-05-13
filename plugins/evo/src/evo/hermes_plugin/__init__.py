"""Hermes runtime plugin — auto-discovered via pip entry-point
`hermes_agent.plugins`.

Hooks registered:
- `on_session_start`: registers the hermes session in evo's inject
  registry. Return value is ignored by hermes.
- `pre_llm_call`: drains pending events at turn start into
  `{"context": "..."}`. Fires once per `hermes chat` turn.
- `transform_tool_result`: drains pending events after each tool
  call and appends them to the tool result the model sees. Without
  this, single-turn mode (`hermes chat -q ... -Q`) only drains at
  turn start — a directive queued *during* the tool-calling loop
  goes unread because there's no next top-level LLM call to fire
  `pre_llm_call` again. Per-tool drain closes that gap.

Session-id handling: `transform_tool_result` doesn't receive the
session_id directly (its signature is tool-scoped). We stash the
session_id from `on_session_start` / `pre_llm_call` into a module
global so the per-tool hook can read it. Single hermes process =
single active session at a time, so this is safe; for subagents
that fork their own session, the parent's drain remains correct
because `transform_tool_result` only fires for the current agent's
tools.

In-process function calls; no fork+exec. We don't use the marker
fast-path optimization because the cost of reading the queue file is
already sub-millisecond and far below model RTT.

See notes/cross-host-inject-design.md.
"""

from __future__ import annotations

from pathlib import Path

from evo.core import repo_root
from evo.inject import marker
from evo.inject.paths import inject_root, exp_events_path, workspace_events_path
from evo.inject.queue import read_events_after, read_offset, write_offset
from evo.inject.registry import get_session, register_session
from evo.inject.drain import format_directive_text


def _resolve_root() -> Path | None:
    """Return the workspace root if we're inside an evo workspace."""
    try:
        root = repo_root()
    except Exception:
        return None
    if not (root / ".evo").exists():
        return None
    if not inject_root(root).parent.exists():
        return None
    return root


def _ensure_registered(root: Path, session_id: str) -> None:
    """Register the hermes session if not already in the registry."""
    if get_session(root, session_id) is None:
        register_session(root, session_id, "hermes")


def _compute_drain_text(root: Path, session_id: str) -> str | None:
    """Read pending events for `session_id`, format, update offset, unlink
    marker. Returns the formatted text or None if nothing to deliver.
    Mirrors evo.inject.drain.drain_session minus stdout I/O."""
    sess = get_session(root, session_id)
    if sess is None:
        marker.unlink(root, session_id)
        return None
    exp_id = sess.get("exp_id")
    events: list[dict] = []
    new_workspace_offset: str | None = None
    new_exp_offset: str | None = None

    if exp_id:
        last_id = read_offset(root, session_id, "exp")
        new_events = read_events_after(exp_events_path(root, exp_id), last_id)
        events.extend(new_events)
        if new_events:
            new_exp_offset = new_events[-1]["id"]
    else:
        last_id = read_offset(root, session_id, "workspace")
        new_events = read_events_after(workspace_events_path(root), last_id)
        events.extend(new_events)
        if new_events:
            new_workspace_offset = new_events[-1]["id"]

    text = format_directive_text(events) if events else None
    if new_workspace_offset or new_exp_offset:
        write_offset(
            root,
            session_id,
            workspace_id=new_workspace_offset,
            exp_id=new_exp_offset,
        )
    marker.unlink(root, session_id)
    return text or None


# Stash the most-recent session_id from on_session_start / pre_llm_call
# so `transform_tool_result` (which doesn't receive session_id) can drain
# into the right session's offset.
_LAST_SESSION_ID: str | None = None


def _stash_session(session_id: str | None) -> None:
    global _LAST_SESSION_ID
    if session_id:
        _LAST_SESSION_ID = session_id


def _on_session_start(session_id: str | None = None, **kwargs):
    """Register the session. No drain — hermes ignores this hook's
    return value; pre_llm_call is the only context-injection point."""
    if not session_id:
        return None
    _stash_session(session_id)
    root = _resolve_root()
    if root is None:
        return None
    _ensure_registered(root, session_id)
    return None


def _on_pre_llm_call(session_id: str | None = None, **kwargs):
    """Per-turn drain. Always reads the queue (in-process is cheap).
    Returns {"context": "..."} when there's content to inject."""
    if not session_id:
        return None
    _stash_session(session_id)
    root = _resolve_root()
    if root is None:
        return None
    _ensure_registered(root, session_id)
    text = _compute_drain_text(root, session_id)
    if text:
        return {"context": text}
    return None


def _on_transform_tool_result(
    tool_name: str | None = None,
    arguments: dict | None = None,
    result: str | None = None,
    task_id: str | None = None,
    **kwargs,
):
    """Per-tool drain. Fires after each tool call; appends any pending
    directive text to the tool result so the model sees the directive
    on its very next iteration through the tool-calling loop.

    Returns the modified result string, or None to leave it unchanged
    (no directive to deliver). Hermes treats `None`/no return as 'leave
    result unchanged' per its hook contract."""
    session_id = kwargs.get("session_id") or _LAST_SESSION_ID
    if not session_id:
        return None
    root = _resolve_root()
    if root is None:
        return None
    _ensure_registered(root, session_id)
    text = _compute_drain_text(root, session_id)
    if not text:
        return None
    # Suffix the directive after the original tool result. The
    # `[evo direct] ...` prefix already comes from format_directive_text;
    # adding a separator line makes the boundary clear to the model.
    base = result if result is not None else ""
    return f"{base}\n\n--- evo directive ---\n{text}"


def register(ctx) -> None:
    """Hermes plugin entry point — invoked once at plugin load."""
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("transform_tool_result", _on_transform_tool_result)
