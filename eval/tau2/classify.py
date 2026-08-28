"""Failure taxonomy and dsh JSONL scans for τ² eval scale-out.

Labels match the stage-6 plan: user-sim early stop, model refused to write,
tool-arg errors, and COMMUNICATE misses. DB zeros stay as their own tag.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

READ_PREFIXES = ("get_", "list_", "search_", "find_", "calculate_")


@dataclass
class JsonlScan:
    """Counts from one task's dsh session JSONL."""

    mcp_calls: int = 0
    bash_calls: int = 0
    compaction_events: int = 0
    tool_errors: int = 0
    called_tools: list[str] = field(default_factory=list)
    write_tools: list[str] = field(default_factory=list)


def domain_tool_bare_name(name: str) -> str:
    """Strip the dsh MCP prefix so names match τ toolkit methods."""
    if name.startswith("mcp__tau2__"):
        return name[len("mcp__tau2__") :]
    return name


def is_read_tool(name: str) -> bool:
    """True for lookup-style domain tools that do not change the DB."""
    return domain_tool_bare_name(name).startswith(READ_PREFIXES)


def scan_jsonl(paths: list[Path]) -> JsonlScan:
    """Count MCP vs bash calls, compaction events, and tool errors."""
    scan = JsonlScan()
    seen: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_type = str(event.get("type") or "")
                data = event.get("data") or {}
                if event_type.startswith("compaction/"):
                    scan.compaction_events += 1
                if event_type in {"tool/call", "tool/request"}:
                    name = data.get("name") or (data.get("message") or {}).get("name")
                    if not isinstance(name, str):
                        continue
                    if name.startswith("mcp__tau2__"):
                        scan.mcp_calls += 1
                    elif name == "bash":
                        scan.bash_calls += 1
                    if name not in seen:
                        seen.add(name)
                        scan.called_tools.append(name)
                    if name.startswith("mcp__tau2__") and not is_read_tool(name):
                        if name not in scan.write_tools:
                            scan.write_tools.append(name)
                if event_type in {"tool/result", "tool/error"}:
                    if data.get("isError") or data.get("error") or event_type == "tool/error":
                        scan.tool_errors += 1
    return scan


def classify(
    *,
    reward: float | None,
    reward_breakdown: dict[str, Any] | None,
    db_reward: float | None,
    termination: str | None,
    scan: JsonlScan,
    error: str | None = None,
) -> str:
    """One-line fail_reason. `pass` when official reward is 1.0."""
    if error:
        return f"error:{error}"
    if reward == 1.0:
        return "pass"
    parts: list[str] = []
    breakdown = {str(key): value for key, value in (reward_breakdown or {}).items()}
    db_zero = _is_zero(breakdown.get("DB")) or db_reward == 0
    communicate_zero = _is_zero(breakdown.get("COMMUNICATE"))
    if db_zero and "DB" not in parts:
        parts.append("DB")
    if communicate_zero:
        parts.append("COMMUNICATE")
    for key, value in breakdown.items():
        if key in {"DB", "COMMUNICATE"}:
            continue
        if _is_zero(value) and key not in parts:
            parts.append(key)
    term = _termination(termination)
    if term in {"timeout", "max_steps", "too_many_errors", "agent_error", "user_error", "unexpected_error", "infrastructure_error"}:
        parts.append(term)
    no_tools = scan.mcp_calls == 0 and scan.bash_calls == 0
    if scan.tool_errors and db_zero:
        parts.append("tool_arg_error")
    if db_zero and scan.write_tools:
        pass
    elif db_zero and not no_tools:
        parts.append("model_refused_write")
    if term == "user_stop" and db_zero and no_tools:
        parts.append("user_sim_early_stop")
    if not parts:
        if term and term not in {"user_stop", "agent_stop"}:
            parts.append(term)
        else:
            parts.append("reward!=1")
    return ",".join(parts)


def _termination(value: Any) -> str:
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    return str(raw).lower()


def _is_zero(value: Any) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def self_test() -> None:
    """Pin the four stage-6 buckets without running a simulation."""
    empty = JsonlScan()
    if classify(reward=1.0, reward_breakdown={"DB": 1, "COMMUNICATE": 1}, db_reward=1.0, termination="user_stop", scan=empty) != "pass":
        raise SystemExit("classify pass")
    comm = classify(
        reward=0.0,
        reward_breakdown={"DB": 1, "COMMUNICATE": 0},
        db_reward=1.0,
        termination="user_stop",
        scan=JsonlScan(mcp_calls=2, write_tools=["mcp__tau2__book_reservation"]),
    )
    if comm != "COMMUNICATE":
        raise SystemExit(f"classify COMMUNICATE miss: {comm}")
    refused = classify(
        reward=0.0,
        reward_breakdown={"DB": 0, "COMMUNICATE": 1},
        db_reward=0.0,
        termination="user_stop",
        scan=JsonlScan(mcp_calls=2, called_tools=["mcp__tau2__get_user_details"], write_tools=[]),
    )
    if "model_refused_write" not in refused or "DB" not in refused:
        raise SystemExit(f"classify model refused: {refused}")
    early = classify(
        reward=0.0,
        reward_breakdown={"DB": 0, "COMMUNICATE": 0},
        db_reward=0.0,
        termination="user_stop",
        scan=empty,
    )
    if "user_sim_early_stop" not in early:
        raise SystemExit(f"classify user-sim early stop: {early}")
    args_err = classify(
        reward=0.0,
        reward_breakdown={"DB": 0, "COMMUNICATE": 1},
        db_reward=0.0,
        termination="user_stop",
        scan=JsonlScan(mcp_calls=3, write_tools=["mcp__tau2__book_reservation"], tool_errors=1),
    )
    if "tool_arg_error" not in args_err or "DB" not in args_err:
        raise SystemExit(f"classify tool arg error: {args_err}")
    if classify(reward=None, reward_breakdown=None, db_reward=None, termination=None, scan=empty, error="boom") != "error:boom":
        raise SystemExit("classify error")
    print("classify self_test ok")
