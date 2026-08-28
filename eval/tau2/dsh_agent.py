"""τ HalfDuplexAgent that forwards each user turn to DeepSeekHarness.run().

Stage 4 starts the HTTP+MCP bridge, then starts DeepSeekHarness with
DSH_TAU2_MCP_URL so initialize discovers mcp__tau2__* tools. Domain writes
go through that toolkit; generate_next_message returns user-facing text only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from uuid import uuid4

from tau2.agent.base_agent import HalfDuplexAgent, ValidAgentInputMessage
from tau2.data_model.message import AssistantMessage, Message, MultiToolMessage, ToolMessage, UserMessage
from tau2.data_model.tasks import Task
from tau2.environment.tool import Tool

from deepseek_harness import DeepSeekHarness

from env_bridge import BridgeCall, EnvBridge, toolkit_from_tools
from prompts import write_task_workspace

_launch: "HarnessLaunch | None" = None
_workspace: Path | None = None
_last_task_workspace: Path | None = None


@dataclass(frozen=True)
class HarnessLaunch:
    """Constructor fields for DeepSeekHarness, started after MCP is up.

    `cwd` is the eval work root. Each task's DSH_CWD is `workspaces/{task_id}-{hex}`
    under that root, computed in `create_dsh_agent`.
    """

    provider: str
    model: str
    cwd: str
    runtime_cwd: str
    session_root: str
    cordis: str
    launch_args_override: tuple[str, ...]
    request_timeout_seconds: float
    extra_env: dict[str, str]


def set_harness_launch(launch: HarnessLaunch) -> None:
    """Install the per-task runtime constructor used after the MCP bridge listens."""
    global _launch
    _launch = launch


def set_workspace(workspace: Path) -> None:
    """Install the eval work root. Per-task DSH_CWD is a child of `workspaces/`."""
    global _workspace
    _workspace = workspace


def last_task_workspace() -> Path | None:
    """DSH_CWD of the most recently constructed agent, if any."""
    return _last_task_workspace


def _require_launch() -> HarnessLaunch:
    if _launch is None:
        raise RuntimeError("call set_harness_launch() before create_dsh_agent()")
    return _launch


def _require_workspace() -> Path:
    if _workspace is None:
        raise RuntimeError("call set_workspace() before create_dsh_agent()")
    return _workspace


def _start_harness(mcp_url: str | None, cwd: str) -> DeepSeekHarness:
    spec = _require_launch()
    env = dict(spec.extra_env)
    if mcp_url is not None:
        env["DSH_TAU2_MCP_URL"] = mcp_url
    harness = DeepSeekHarness(
        provider=spec.provider,
        model=spec.model,
        cwd=cwd,
        runtime_cwd=spec.runtime_cwd,
        session_root=spec.session_root,
        cordis=spec.cordis,
        launch_args_override=spec.launch_args_override,
        request_timeout_seconds=spec.request_timeout_seconds,
        env=env,
    )
    try:
        harness.start()
    except Exception:
        harness.close()
        raise
    return harness


@dataclass
class DshAgentState:
    """Per-task session identity; conversation history lives in the dsh session log."""

    session_id: str
    base_url: str | None = None
    mcp_url: str | None = None


class DshHalfDuplexAgent(HalfDuplexAgent[DshAgentState]):
    """One τ task = one dsh process started after that task's MCP URL is listening."""

    def __init__(
        self,
        tools: list[Tool],
        domain_policy: str,
        workspace: Path,
        task: Optional[Task] = None,
    ):
        super().__init__(tools=tools, domain_policy=domain_policy)
        self._workspace = workspace
        self._task = task
        self._bridge: EnvBridge | None = None
        self._harness: DeepSeekHarness | None = None
        self.turn_calls: list[list[BridgeCall]] = []

    def get_init_state(self, message_history: Optional[list[Message]] = None) -> DshAgentState:
        """Start MCP+HTTP on this task's toolkit, then start dsh with DSH_TAU2_MCP_URL."""
        self.turn_calls = []
        task_id = self._task.id if self._task is not None else "notask"
        state = DshAgentState(session_id=f"{task_id}-{uuid4().hex[:8]}")
        if not self.tools:
            write_task_workspace(self._workspace, self.domain_policy, None, [])
            self._harness = _start_harness(None, str(self._workspace))
            return state
        toolkit = toolkit_from_tools(self.tools)
        self._bridge = EnvBridge(toolkit, self.tools)
        try:
            state.base_url = self._bridge.start()
            state.mcp_url = self._bridge.mcp_url
            write_task_workspace(
                self._workspace,
                self.domain_policy,
                state.base_url,
                self.tools,
                mcp_url=state.mcp_url,
            )
            self._harness = _start_harness(state.mcp_url, str(self._workspace))
        except Exception:
            if self._harness is not None:
                self._harness.close()
                self._harness = None
            self._bridge.stop()
            self._bridge = None
            raise
        return state

    def generate_next_message(
        self,
        message: ValidAgentInputMessage,
        state: DshAgentState,
    ) -> tuple[AssistantMessage, DshAgentState]:
        """Forward one user utterance to dsh. Domain mutations happen inside that run()."""
        if isinstance(message, (ToolMessage, MultiToolMessage)):
            raise TypeError(
                "DshHalfDuplexAgent received a tool result; return text only, "
                "not AssistantMessage.tool_calls, or Orchestrator will mutate the DB twice"
            )
        if not isinstance(message, UserMessage):
            raise TypeError(f"expected UserMessage, got {type(message).__name__}")
        if self._harness is None:
            raise RuntimeError("get_init_state() must start the harness before generate_next_message()")
        user_text = (message.content or "").strip()
        if not user_text:
            # Empty user-simulator turns still need a projection slot.
            self.turn_calls.append([])
            return AssistantMessage.text("I didn't catch that. Could you repeat?"), state
        mark = 0 if self._bridge is None else len(self._bridge.calls)
        result = self._harness.run(user_text, session_id=state.session_id)
        if self._bridge is not None:
            self.turn_calls.append(list(self._bridge.calls[mark:]))
        else:
            self.turn_calls.append([])
        text = (result.final_response or "").strip() or "(empty)"
        return AssistantMessage.text(text), state

    def stop(self, message: Optional[ValidAgentInputMessage] = None, state: Optional[DshAgentState] = None) -> None:
        """Close the dsh runtime, then the bridge. turn_calls stay for scoring projection."""
        if self._harness is not None:
            self._harness.close()
            self._harness = None
        if self._bridge is not None:
            self._bridge.stop()
            self._bridge = None


def _assistant_tools_only(tools: list[Tool]) -> list[Tool]:
    """Drop user-simulator toolkits (telecom `TelecomUserTools`). Non-solo already omits them."""
    kept: list[Tool] = []
    dropped: list[str] = []
    for tool in tools:
        toolkit = getattr(getattr(tool, "_func", None), "__self__", None)
        type_name = type(toolkit).__name__ if toolkit is not None else ""
        if "User" in type_name:
            dropped.append(str(getattr(tool, "name", None) or type_name))
            continue
        kept.append(tool)
    if dropped:
        print(f"filtered_user_tools={dropped}")
    return kept if kept else tools


def _task_workspace(work_root: Path, task_id: str) -> Path:
    """New directory under work_root/workspaces for this trial."""
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in task_id).strip("-._")
    if not safe:
        safe = "task"
    if len(safe) > 80:
        safe = safe[:80].rstrip("-._")
    workspace = work_root / "workspaces" / f"{safe}-{uuid4().hex[:8]}"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def create_dsh_agent(tools: list[Tool], domain_policy: str, **kwargs: object) -> DshHalfDuplexAgent:
    """Registry factory. τ's build_agent passes tools, domain_policy, llm, llm_args, task."""
    global _last_task_workspace
    tools = _assistant_tools_only(tools)
    task = kwargs.get("task")
    typed = task if isinstance(task, Task) else None
    task_id = typed.id if typed is not None else "notask"
    workspace = _task_workspace(_require_workspace(), task_id)
    _last_task_workspace = workspace
    return DshHalfDuplexAgent(
        tools=tools,
        domain_policy=domain_policy,
        workspace=workspace,
        task=typed,
    )
