"""Project HTTP/MCP-bridge toolkit calls onto a τ trajectory for official scoring.

τ EnvironmentEvaluator replays AssistantMessage.tool_calls onto a fresh
environment. DshHalfDuplexAgent returns user-facing text only so the
Orchestrator will not execute domain tools a second time. After
orchestrator.run() and before evaluate_simulation(), this module inserts
the recorded calls in the same message pattern a native LLMAgent would
have produced.
"""

from __future__ import annotations

from loguru import logger
from tau2.data_model.message import AssistantMessage, Message, ToolCall, ToolMessage
from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
from tau2.orchestrator.full_duplex_orchestrator import FullDuplexOrchestrator
from tau2.orchestrator.modes import CommunicationMode

from env_bridge import BridgeCall


def project_domain_calls(
    messages: list[Message],
    turn_calls: list[list[BridgeCall]],
    *,
    skip_first_assistant_text: bool = True,
) -> list[Message]:
    """Insert one AssistantMessage plus ToolMessage pair per recorded bridge call.

    Each harness.run() maps to one user-facing AssistantMessage. Non-solo τ
    injects the opening greeting without calling the agent; that first text is
    skipped unless skip_first_assistant_text is False. User tool_calls already
    on the trajectory stay in place. Does not mutate `messages`.
    """
    projected: list[Message] = []
    turn_idx = 0
    skipped_opening = not skip_first_assistant_text
    for message in messages:
        user_facing = (
            isinstance(message, AssistantMessage)
            and message.has_text_content()
            and not message.is_tool_call()
        )
        if user_facing:
            if not skipped_opening:
                skipped_opening = True
                projected.append(message)
                continue
            if turn_idx < len(turn_calls):
                projected.extend(_messages_for_turn(turn_calls[turn_idx], turn_idx))
                turn_idx += 1
            projected.append(message)
        else:
            projected.append(message)
    if turn_idx != len(turn_calls):
        logger.warning(
            "domain-call projection consumed {used}/{total} harness turns",
            used=turn_idx,
            total=len(turn_calls),
        )
    return projected


def install_run_simulation_hook() -> None:
    """Replace τ run_simulation so evaluation sees projected domain calls."""
    import tau2.runner as runner_mod
    import tau2.runner.batch as batch_mod
    import tau2.runner.simulation as sim_mod

    if getattr(sim_mod.run_simulation, "_dsh_domain_projection", False):
        return

    def run_simulation(orchestrator, *, evaluation_type=EvaluationType.ALL, env_kwargs=None):
        simulation = orchestrator.run()
        simulation.policy = orchestrator.environment.get_policy()
        agent = getattr(orchestrator, "agent", None)
        turn_calls = getattr(agent, "turn_calls", None)
        solo_mode = getattr(orchestrator, "solo_mode", False)
        if turn_calls is not None:
            simulation.messages = project_domain_calls(
                list(simulation.messages or []),
                turn_calls,
                skip_first_assistant_text=not solo_mode,
            )
        domain = orchestrator.environment.get_domain_name()
        task = orchestrator.task
        is_full_duplex = isinstance(orchestrator, FullDuplexOrchestrator)
        mode = (
            CommunicationMode.FULL_DUPLEX
            if is_full_duplex
            else CommunicationMode.HALF_DUPLEX
        )
        reward_info = evaluate_simulation(
            simulation=simulation,
            task=task,
            evaluation_type=evaluation_type,
            solo_mode=solo_mode,
            domain=domain,
            mode=mode,
            env_kwargs=env_kwargs,
        )
        simulation.reward_info = reward_info
        logger.info(
            "Simulation complete: domain={domain}, task={task_id}, reward={reward}",
            domain=domain,
            task_id=task.id,
            reward=reward_info.reward,
        )
        return simulation

    run_simulation._dsh_domain_projection = True
    sim_mod.run_simulation = run_simulation
    batch_mod.run_simulation = run_simulation
    runner_mod.run_simulation = run_simulation


def self_test() -> None:
    """MCP create_task plus projection must make EnvironmentEvaluator DB-match."""
    from tau2.data_model.message import UserMessage
    from tau2.domains.mock.environment import get_environment
    from tau2.evaluator.evaluator_env import EnvironmentEvaluator
    from tau2.runner import get_tasks

    from env_bridge import EnvBridge, mcp_call_create_task, toolkit_from_tools

    tasks = get_tasks("mock", task_ids=["create_task_1"])
    task = tasks[0]
    environment = get_environment()
    toolkit = environment.tools
    assert toolkit is not None
    tools = list(toolkit.get_tools().values())
    bridge = EnvBridge(toolkit, tools)
    bridge.start()
    mcp_url = bridge.mcp_url
    assert mcp_url is not None
    try:
        mcp_call_create_task(mcp_url)
        recorded = [list(bridge.calls)]
    finally:
        bridge.stop()

    trajectory = [
        AssistantMessage.text("Hi! How can I help you today?"),
        UserMessage.text("Create a task called Important Meeting for user_1."),
        AssistantMessage.text("All set! I created the task Important Meeting."),
    ]
    constructor = get_environment
    before = EnvironmentEvaluator.calculate_reward(
        environment_constructor=constructor,
        task=task,
        full_trajectory=trajectory,
        solo_mode=False,
    )
    if before.db_check is None or before.db_check.db_match:
        raise SystemExit(f"unprojected trajectory should miss DB, got {before}")
    projected = project_domain_calls(trajectory, recorded)
    after = EnvironmentEvaluator.calculate_reward(
        environment_constructor=constructor,
        task=task,
        full_trajectory=projected,
        solo_mode=False,
    )
    if after.db_check is None or not after.db_check.db_match:
        raise SystemExit(f"projected trajectory should match DB, got {after}")
    print("ok: unprojected DB=0, projected DB=1")


def _messages_for_turn(calls: list[BridgeCall], turn_idx: int) -> list[Message]:
    """One native-style tool_call plus result per recorded bridge invocation."""
    messages: list[Message] = []
    for index, call in enumerate(calls):
        call_id = f"dsh-{turn_idx}-{index}"
        tool_call = ToolCall(
            id=call_id,
            name=call.name,
            arguments=call.arguments,
            requestor="assistant",
        )
        messages.append(
            AssistantMessage(role="assistant", content=None, tool_calls=[tool_call])
        )
        messages.append(
            ToolMessage(
                id=call_id,
                role="tool",
                content=call.content,
                requestor="assistant",
                error=False,
            )
        )
    return messages
