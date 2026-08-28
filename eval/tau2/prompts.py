"""Per-task AGENTS.md / TOOLS.md for the τ² eval workspace."""

from __future__ import annotations

from pathlib import Path

from tau2.environment.tool import Tool

from env_bridge import write_tools_markdown

OPENING = "Hi! How can I help you today?"


def write_agents_markdown(
    workspace: Path,
    policy: str,
    base_url: str | None,
    mcp_url: str | None = None,
) -> Path:
    """Write AGENTS.md: role, opening, domain-API rules, and the full policy."""
    api = ""
    if base_url is not None:
        mcp = mcp_url or f"{base_url}/mcp"
        api = (
            "Change reservations, orders, accounts, or tasks only through the domain toolkit.\n"
            f"Prefer the MCP tools named mcp__tau2__<tool> (server {mcp}).\n"
            f"HTTP backup is {base_url}; localhost curl is allowed if an MCP tool is missing.\n"
            "The bash 'no internet' note does not apply to this localhost API.\n"
            "Local files do not complete the customer's request.\n\n"
        )
    text = (
        "You are a customer-service agent. Follow the domain policy below.\n"
        f"The conversation already started; you already said: {OPENING!r}. Do not greet again.\n"
        "Reply to the customer in natural language. Do not paste curl commands or raw JSON.\n"
        "If information is missing, ask the customer.\n\n"
        f"{api}"
        f"## Domain policy\n\n{policy.strip()}\n"
    )
    path = workspace / "AGENTS.md"
    path.write_text(text, encoding="utf-8")
    return path


def write_task_workspace(
    workspace: Path,
    policy: str,
    base_url: str | None,
    tools: list[Tool],
    mcp_url: str | None = None,
) -> None:
    """Write AGENTS.md plus TOOLS.md / ENV_API.txt for one τ task workspace."""
    workspace.mkdir(parents=True, exist_ok=True)
    write_agents_markdown(workspace, policy, base_url, mcp_url)
    if base_url is not None and tools:
        write_tools_markdown(workspace, base_url, tools, mcp_url=mcp_url)


def policy_needle(policy: str) -> str:
    """A stable substring of policy prose to search for in a session JSONL."""
    for line in policy.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if len(stripped) >= 24:
            return stripped
    return policy.strip()[:40]


def self_test() -> None:
    """Two workspaces must keep distinct policy text and API ports."""
    import tempfile

    from tau2.domains.mock.environment import get_environment

    environment = get_environment()
    assert environment.tools is not None
    tools = list(environment.tools.get_tools().values())
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        first = root / "task-a"
        second = root / "task-b"
        write_task_workspace(
            first,
            "policy-alpha-unique-marker-aaaa",
            "http://127.0.0.1:1111",
            tools,
            mcp_url="http://127.0.0.1:1111/mcp",
        )
        write_task_workspace(
            second,
            "policy-beta-unique-marker-bbbb",
            "http://127.0.0.1:2222",
            tools,
            mcp_url="http://127.0.0.1:2222/mcp",
        )
        agents_a = (first / "AGENTS.md").read_text(encoding="utf-8")
        agents_b = (second / "AGENTS.md").read_text(encoding="utf-8")
        if "policy-alpha-unique-marker-aaaa" not in agents_a:
            raise SystemExit("task-a AGENTS.md missing its policy")
        if "policy-beta-unique-marker-bbbb" not in agents_b:
            raise SystemExit("task-b AGENTS.md missing its policy")
        if "policy-beta-unique-marker-bbbb" in agents_a or "policy-alpha-unique-marker-aaaa" in agents_b:
            raise SystemExit("task workspaces leaked each other's policy")
        env_a = (first / "ENV_API.txt").read_text(encoding="utf-8")
        env_b = (second / "ENV_API.txt").read_text(encoding="utf-8")
        if "http://127.0.0.1:1111" not in env_a or "http://127.0.0.1:1111/mcp" not in env_a:
            raise SystemExit("task-a ENV_API.txt missing HTTP or MCP URL")
        if "http://127.0.0.1:2222" not in env_b or "http://127.0.0.1:2222/mcp" not in env_b:
            raise SystemExit("task-b ENV_API.txt missing HTTP or MCP URL")
        if "mcp__tau2__" not in (first / "TOOLS.md").read_text(encoding="utf-8"):
            raise SystemExit("task-a TOOLS.md missing MCP tool names")
        if "mcp__tau2__" not in agents_a:
            raise SystemExit("task-a AGENTS.md missing MCP tool names")
    print("ok: per-task AGENTS.md and PORT are isolated")
