#!/usr/bin/env python3
"""Register dsh_agent and run τ tasks through run_single_task or run_domain.

Stage 4 starts HTTP+MCP on the Orchestrator toolkit, then starts DeepSeekHarness
with DSH_TAU2_MCP_URL so initialize discovers mcp__tau2__* tools. Domain calls
are projected onto the τ trajectory after run(). Stage 5 selects a composition
with --layer / --cordis and can run the pinned mock+airline suite. Stage 6 uses
`--split base` so `run_domain` writes `$TAU2_DATA_DIR/simulations/<save-to>/`.
Run from the repository root. Keep `--max-concurrency 1` and `workers=0`:
the factory, workspace, and projection hook are in-process globals.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_TAU2 = Path(os.environ.get("TAU2_ROOT", str(Path.home() / "Desktop/projects/tau2-bench"))).expanduser()
DEFAULT_CORDIS = EVAL_DIR / "cordis.eval.yml"
RUNTIME_BIN = REPO_ROOT / "packages/examples/jsonrpc-demo/src/bin.ts"
SEED = 42

# First 5 airline test-split ids. Rerun with the same --layer and this list.
STAGE5_AIRLINE_TASK_IDS = ("2", "6", "8", "13", "16")
STAGE5_SUITE: tuple[tuple[str, str], ...] = (
    ("mock", "create_task_1"),
    *(("airline", task_id) for task_id in STAGE5_AIRLINE_TASK_IDS),
)

LAYERS: dict[str, str] = {
    "baseline": "cordis.eval.yml",
    "5a": "cordis.eval.5a.yml",
    "5b": "cordis.eval.5b.yml",
    "5c": "cordis.eval.full.yml",
    "full": "cordis.eval.full.yml",
    "ablation": "cordis.eval.ablation.yml",
    "5e": "cordis.eval.5e.yml",
}


@dataclass(frozen=True)
class TaskSpec:
    """One τ domain + task id."""

    domain: str
    task_id: str


@dataclass
class TaskOutcome:
    """One trial's printed score and the JSONL signals used to explain failures."""

    spec: TaskSpec
    reward: float | None = None
    reward_breakdown: dict[str, Any] | None = None
    db_reward: float | None = None
    termination: str | None = None
    called_tools: list[str] = field(default_factory=list)
    first_request_tools: list[str] = field(default_factory=list)
    policy_in_session_jsonl: bool | None = None
    error: str | None = None
    workspace: str | None = None
    mcp_calls: int = 0
    bash_calls: int = 0
    compaction_events: int = 0
    tool_errors: int = 0

    @property
    def passed(self) -> bool:
        """True when official reward is 1.0."""
        return self.reward == 1.0

    @property
    def fail_reason(self) -> str:
        """One-line cause: exception, missing DB/COMMUNICATE, or stage-6 taxonomy."""
        from classify import JsonlScan, classify, is_read_tool

        return classify(
            reward=self.reward,
            reward_breakdown=self.reward_breakdown,
            db_reward=self.db_reward,
            termination=self.termination,
            scan=JsonlScan(
                mcp_calls=self.mcp_calls,
                bash_calls=self.bash_calls,
                compaction_events=self.compaction_events,
                tool_errors=self.tool_errors,
                called_tools=self.called_tools,
                write_tools=[name for name in self.called_tools if name.startswith("mcp__tau2__") and not is_read_tool(name)],
            ),
            error=self.error,
        )


def main() -> None:
    """Optional --check-hash, otherwise start dsh after MCP and run selected tasks."""
    if not (REPO_ROOT / "pnpm-workspace.yaml").is_file():
        sys.exit(f"expected repository root at {REPO_ROOT}")

    parser = argparse.ArgumentParser()
    parser.add_argument("--tau2-root", type=Path, default=Path(os.environ.get("TAU2_ROOT", DEFAULT_TAU2)))
    parser.add_argument("--domain", default="mock")
    parser.add_argument("--task-id", action="append", dest="task_ids", default=None)
    parser.add_argument("--user-llm", default=os.environ.get("TAU2_USER_LLM", "deepseek/deepseek-v4-flash"))
    parser.add_argument("--model", default=os.environ.get("DSH_MODEL", "deepseek-v4-flash"))
    parser.add_argument("--cordis", type=Path, default=None, help="Eval composition YAML (overrides --layer)")
    parser.add_argument(
        "--layer",
        choices=sorted(LAYERS),
        help="Named composition: baseline, 5a, 5b, 5c/full, ablation, 5e",
    )
    parser.add_argument(
        "--layers",
        default=None,
        help="Comma-separated --layer names run in order (stage-5 sweep)",
    )
    parser.add_argument("--work", type=Path, default=None, help="Session/workspace parent directory")
    parser.add_argument(
        "--suite",
        action="store_true",
        help="Run mock create_task_1 plus airline test-split ids 2,6,8,13,16",
    )
    parser.add_argument(
        "--stop-on-collapse",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip the rest of a layer (and later --layers) when mock create_task_1 reward is 0",
    )
    parser.add_argument(
        "--split",
        default=None,
        help="τ task split (airline/retail/telecom `base` is the official text eval set). Uses run_domain.",
    )
    parser.add_argument("--num-tasks", type=int, default=None, help="Cap tasks from the split (omit for the full split)")
    parser.add_argument("--num-trials", type=int, default=1)
    parser.add_argument(
        "--save-to",
        default=None,
        help="Directory name under $TAU2_DATA_DIR/simulations/ (default dsh-{layer}-{domain}-{split})",
    )
    parser.add_argument("--timeout", type=float, default=900, help="Per-simulation wallclock seconds (τ --timeout)")
    parser.add_argument("--max-steps", type=int, default=200, help="User↔agent turns only; dsh inner tools do not count")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument(
        "--check-hash",
        action="store_true",
        help="HTTP+MCP writes, projected DB scoring, and per-task AGENTS.md isolation (no dsh)",
    )
    args = parser.parse_args()

    tau2_root = args.tau2_root.resolve()
    if not (tau2_root / "src/tau2").is_dir():
        sys.exit(f"tau2 checkout not found at {tau2_root} (set --tau2-root or TAU2_ROOT)")
    os.environ.setdefault("TAU2_DATA_DIR", str(tau2_root / "data"))

    try:
        from tau2.data_model.simulation import TextRunConfig
        from tau2.registry import registry
        from tau2.runner import get_tasks, run_single_task
    except ImportError:
        sys.exit(
            f"import tau2 failed in {sys.executable}\n"
            "Install tau2 into THAT interpreter (not a different .venv):\n"
            f'  uv pip install --python {sys.executable} -e "{tau2_root}"\n'
            "tau2 needs Python >=3.12,<3.14. `uv run --project python/sdk` uses "
            "tmp/py-sdk-venv and ignores an activated .venv unless you pass --active."
        )

    if args.check_hash:
        from classify import self_test as classify_self_test
        from env_bridge import self_test
        from project_trajectory import self_test as projection_self_test
        from prompts import self_test as workspace_self_test

        self_test()
        projection_self_test()
        workspace_self_test()
        classify_self_test()
        return

    if not os.environ.get("DEEPSEEK_API_KEY"):
        sys.exit("set DEEPSEEK_API_KEY")
    if args.suite and args.split:
        sys.exit("use either --suite or --split, not both")
    if args.domain == "banking_knowledge":
        sys.exit("banking_knowledge is a later domain; do not mix retrieval/shell with airline")

    layer_names = _parse_layers(args.layers, args.layer)
    specs = [] if args.split else _task_specs(args.suite, args.domain, args.task_ids)
    from dsh_agent import create_dsh_agent
    from project_trajectory import install_run_simulation_hook

    registry.register_agent_factory(create_dsh_agent, "dsh_agent")
    install_run_simulation_hook()

    all_outcomes: list[tuple[str, list[TaskOutcome]]] = []
    collapsed = False
    save_paths: list[str] = []
    for layer_name in layer_names:
        if collapsed and args.stop_on_collapse:
            print(f"skip layer={layer_name} (previous mock collapsed)")
            continue
        cordis = args.cordis.resolve() if args.cordis is not None else EVAL_DIR / LAYERS[layer_name]
        if not cordis.is_file():
            sys.exit(f"missing eval composition {cordis}")
        work = _work_dir(
            args.work,
            layer_name,
            cordis,
            len(layer_names) > 1 or args.layer is not None,
            split=args.split,
            domain=args.domain,
        )
        print(f"layer={layer_name} cordis={cordis} work={work}")
        if args.split:
            outcomes, save_path = _run_split(
                layer_name=layer_name,
                cordis=cordis,
                work=work,
                domain=args.domain,
                split=args.split,
                task_ids=args.task_ids,
                num_tasks=args.num_tasks,
                num_trials=args.num_trials,
                save_to=args.save_to,
                model=args.model,
                user_llm=args.user_llm,
                seed=args.seed,
                timeout=args.timeout,
                max_steps=args.max_steps,
                text_run_config=TextRunConfig,
            )
            save_paths.append(save_path)
        else:
            outcomes = _run_layer(
                layer_name=layer_name,
                cordis=cordis,
                work=work,
                specs=specs,
                model=args.model,
                user_llm=args.user_llm,
                timeout=args.timeout,
                seed=args.seed,
                get_tasks=get_tasks,
                run_single_task=run_single_task,
                text_run_config=TextRunConfig,
                stop_on_collapse=args.stop_on_collapse,
            )
        all_outcomes.append((layer_name, outcomes))
        mock = next((item for item in outcomes if item.spec == TaskSpec("mock", "create_task_1")), None)
        if args.stop_on_collapse and mock is not None and not mock.passed:
            print(f"collapse layer={layer_name} mock_reward={mock.reward} reason={mock.fail_reason}")
            collapsed = True

    _print_summary(all_outcomes)
    if args.work is not None:
        summary_path = args.work.resolve() / "summary.json"
    elif args.split:
        summary_path = EVAL_DIR / ".work" / "stage6" / "summary.json"
    elif layer_names == ["baseline"] and args.layer is None and args.layers is None and not args.suite:
        summary_path = EVAL_DIR / ".work" / "stage4" / "summary.json"
    else:
        summary_path = EVAL_DIR / ".work" / "stage5" / "summary.json"
    _write_summary(summary_path, all_outcomes, save_paths=save_paths, seed=args.seed)
    print(f"summary_json={summary_path}")
    for path in save_paths:
        print(f"tau2_simulations={path}")


def _parse_layers(layers: str | None, layer: str | None) -> list[str]:
    """Resolve --layers, --layer, or the stage-4 default composition."""
    if layers:
        names = [item.strip() for item in layers.split(",") if item.strip()]
        unknown = [name for name in names if name not in LAYERS]
        if unknown:
            sys.exit(f"unknown --layers values {unknown}; choose from {sorted(LAYERS)}")
        return names
    if layer is not None:
        return [layer]
    return ["baseline"]


def _task_specs(suite: bool, domain: str, task_ids: list[str] | None) -> list[TaskSpec]:
    """Suite list, or one domain with optional repeated --task-id."""
    if suite:
        return [TaskSpec(item[0], item[1]) for item in STAGE5_SUITE]
    ids = task_ids if task_ids else ["create_task_1"]
    return [TaskSpec(domain, task_id) for task_id in ids]


def _work_dir(
    explicit: Path | None,
    layer_name: str,
    cordis: Path,
    layered: bool,
    *,
    split: str | None = None,
    domain: str = "mock",
) -> Path:
    """Per-layer work tree: stage6 for --split, stage5 for named layers, else stage4."""
    if explicit is not None:
        return explicit
    if split is not None:
        return EVAL_DIR / ".work" / "stage6" / layer_name / domain
    if layered or layer_name != "baseline" or cordis.resolve() != DEFAULT_CORDIS.resolve():
        return EVAL_DIR / ".work" / "stage5" / layer_name
    return EVAL_DIR / ".work" / "stage4"


def _run_layer(
    *,
    layer_name: str,
    cordis: Path,
    work: Path,
    specs: list[TaskSpec],
    model: str,
    user_llm: str,
    timeout: float,
    seed: int,
    get_tasks: Any,
    run_single_task: Any,
    text_run_config: Any,
    stop_on_collapse: bool,
) -> list[TaskOutcome]:
    """Run each spec sequentially with a fresh workspace and harness cwd."""
    from dsh_agent import last_task_workspace
    from prompts import policy_needle

    outcomes: list[TaskOutcome] = []
    _install_runtime(work, cordis, model, timeout)
    session_root = work / "sessions"
    for spec in specs:
        if stop_on_collapse and outcomes and outcomes[0].spec == TaskSpec("mock", "create_task_1") and not outcomes[0].passed:
            print(f"skip {spec.domain}/{spec.task_id} (mock collapsed)")
            continue
        outcome = TaskOutcome(spec=spec)
        attempts = 0
        while True:
            try:
                tasks = get_tasks(spec.domain, task_ids=[spec.task_id])
                result = run_single_task(
                    text_run_config(
                        domain=spec.domain,
                        agent="dsh_agent",
                        llm_agent="unused-dsh-runtime",
                        llm_user=user_llm,
                        num_trials=1,
                        max_concurrency=1,
                        timeout=timeout,
                    ),
                    tasks[0],
                    seed=seed,
                )
                workspace = last_task_workspace() or work
                outcome.workspace = str(workspace)
                _fill_from_simulation(outcome, result, session_root, workspace)
                needle = policy_needle(getattr(result, "policy", None) or "")
                jsonl_paths = _jsonl_for_workspace(session_root, workspace)
                _print_task(layer_name, result, outcome, jsonl_paths, workspace, needle)
                break
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}".split("\n", 1)[0]
                if attempts == 0 and "UserMessage must have either content or tool_calls" in msg:
                    attempts += 1
                    print(f"retry {spec.domain}/{spec.task_id} after {msg}")
                    continue
                outcome.error = msg
                workspace = last_task_workspace()
                if workspace is not None:
                    outcome.workspace = str(workspace)
                    _apply_jsonl_scan(outcome, _jsonl_for_workspace(session_root, workspace))
                print(f"task_id={spec.task_id} error={outcome.error}")
                break
        outcomes.append(outcome)
    return outcomes


def _install_runtime(work: Path, cordis: Path, model: str, timeout: float) -> None:
    """One work root, one HarnessLaunch, per-task DSH_CWD created in the factory."""
    from dsh_agent import HarnessLaunch, set_harness_launch, set_workspace

    session_root = work / "sessions"
    session_root.mkdir(parents=True, exist_ok=True)
    (work / "workspaces").mkdir(parents=True, exist_ok=True)
    set_workspace(work)
    set_harness_launch(
        HarnessLaunch(
            provider="deepseek-official",
            model=model,
            cwd=str(work),
            runtime_cwd=str(REPO_ROOT),
            session_root=str(session_root),
            cordis=str(cordis),
            launch_args_override=("node", "--import", "tsx", str(RUNTIME_BIN)),
            request_timeout_seconds=timeout,
            extra_env={"DSH_PERMISSION_MODE": "danger-full-access"},
        )
    )


def _run_split(
    *,
    layer_name: str,
    cordis: Path,
    work: Path,
    domain: str,
    split: str,
    task_ids: list[str] | None,
    num_tasks: int | None,
    num_trials: int,
    save_to: str | None,
    model: str,
    user_llm: str,
    seed: int,
    timeout: float,
    max_steps: int,
    text_run_config: Any,
) -> tuple[list[TaskOutcome], str]:
    """Run a τ split through run_domain and write $TAU2_DATA_DIR/simulations/<save-to>/."""
    from tau2.runner import run_domain
    from tau2.utils.utils import DATA_DIR

    _install_runtime(work, cordis, model, timeout)
    run_name = save_to or f"dsh-{layer_name}-{domain}-{split}"
    config = text_run_config(
        domain=domain,
        agent="dsh_agent",
        llm_agent="unused-dsh-runtime",
        llm_user=user_llm,
        num_trials=num_trials,
        max_concurrency=1,
        workers=0,
        task_split_name=split,
        task_ids=task_ids,
        num_tasks=num_tasks,
        save_to=run_name,
        seed=seed,
        max_steps=max_steps,
        timeout=timeout,
        auto_resume=True,
    )
    print(f"save_to={DATA_DIR / 'simulations' / run_name / 'results.json'}")
    print(f"max_concurrency=1 workers=0 timeout={timeout} max_steps={max_steps} seed={seed}")
    results = run_domain(config)
    outcomes = _outcomes_from_results(domain, results, work)
    _print_split_report(layer_name, results, outcomes, work)
    save_path = str(DATA_DIR / "simulations" / run_name / "results.json")
    return outcomes, save_path


def _outcomes_from_results(domain: str, results: Any, work: Path) -> list[TaskOutcome]:
    """Map τ Results.simulations onto TaskOutcome rows for the summary table."""
    session_root = work / "sessions"
    outcomes: list[TaskOutcome] = []
    for sim in results.simulations or []:
        task_id = str(sim.task_id)
        outcome = TaskOutcome(spec=TaskSpec(domain, task_id))
        workspace = _workspace_for_task(work, task_id)
        if workspace is not None:
            outcome.workspace = str(workspace)
            _fill_from_simulation(outcome, sim, session_root, workspace)
        else:
            _fill_from_simulation(outcome, sim, session_root, work)
        outcomes.append(outcome)
    return outcomes


def _workspace_for_task(work: Path, task_id: str) -> Path | None:
    """Newest workspaces/{safe}-* directory for this task id."""
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in task_id).strip("-._")
    if not safe:
        return None
    prefix = safe[:80].rstrip("-._")
    hits = [path for path in (work / "workspaces").glob(f"{prefix}-*") if path.is_dir()]
    if not hits:
        return None
    return max(hits, key=lambda path: path.stat().st_mtime)


def _fill_from_simulation(outcome: TaskOutcome, result: Any, session_root: Path, workspace: Path) -> None:
    """Copy τ reward fields and scan this task's JSONL."""
    from prompts import policy_needle

    outcome.termination = getattr(result, "termination_reason", None)
    info = result.reward_info
    if info is not None:
        outcome.reward = info.reward
        breakdown = info.reward_breakdown
        if breakdown is not None:
            outcome.reward_breakdown = {str(key): _jsonable(value) for key, value in dict(breakdown).items()}
        if info.db_check is not None:
            outcome.db_reward = getattr(info.db_check, "db_reward", None)
    jsonl_paths = _jsonl_for_workspace(session_root, workspace)
    needle = policy_needle(getattr(result, "policy", None) or "")
    outcome.policy_in_session_jsonl = any(_jsonl_contains(path, needle) for path in jsonl_paths) if needle else False
    outcome.first_request_tools = _first_request_tools(jsonl_paths)
    _apply_jsonl_scan(outcome, jsonl_paths)


def _apply_jsonl_scan(outcome: TaskOutcome, jsonl_paths: list[Path]) -> None:
    """Attach MCP/bash/compaction/error counts from dsh JSONL."""
    from classify import scan_jsonl

    scan = scan_jsonl(jsonl_paths)
    outcome.called_tools = scan.called_tools or _called_tool_names(jsonl_paths)
    outcome.mcp_calls = scan.mcp_calls
    outcome.bash_calls = scan.bash_calls
    outcome.compaction_events = scan.compaction_events
    outcome.tool_errors = scan.tool_errors


def _print_split_report(layer_name: str, results: Any, outcomes: list[TaskOutcome], work: Path) -> None:
    """Compact per-task lines plus JSONL catalog totals (no full trajectories)."""
    print(f"layer={layer_name} split_tasks={len(outcomes)}")
    mcp = sum(item.mcp_calls for item in outcomes)
    bash = sum(item.bash_calls for item in outcomes)
    compact = sum(item.compaction_events for item in outcomes)
    print(f"jsonl_totals mcp_calls={mcp} bash_calls={bash} compaction_events={compact}")
    print("task_id\treward\tdb\treason\tmcp\tbash\tcompact")
    for item in outcomes:
        print(
            f"{item.spec.task_id}\t{item.reward}\t{item.db_reward}\t{item.fail_reason}\t"
            f"{item.mcp_calls}\t{item.bash_calls}\t{item.compaction_events}"
        )
    print(f"dsh_work={work}")
    info = getattr(results, "info", None)
    if info is not None:
        print(f"tau2_info_agent={getattr(info, 'agent', None)} user={getattr(info, 'user', None)}")


def _jsonl_for_workspace(session_root: Path, workspace: Path) -> list[Path]:
    """JSONL files whose session directory encodes this workspace path."""
    marker = "-".join(workspace.parts)
    return [path for path in session_root.rglob("*.jsonl") if marker in str(path) or workspace.name in str(path)]


def _print_task(
    layer_name: str,
    result: Any,
    outcome: TaskOutcome,
    jsonl_paths: list[Path],
    workspace: Path,
    needle: str,
) -> None:
    """Keep the stage-4 per-task stdout so a single-task run stays greppable."""
    print(f"layer={layer_name}")
    print(f"task_id={result.task_id}")
    print(f"termination={result.termination_reason}")
    info = result.reward_info
    if info is None:
        print("reward=None")
    else:
        print(f"reward={info.reward}")
        print(f"reward_basis={info.reward_basis}")
        print(f"reward_breakdown={info.reward_breakdown}")
        if info.db_check is not None:
            print(f"db_check={info.db_check}")
    print("trajectory:")
    for message in result.messages or []:
        kind = "tool_calls" if getattr(message, "tool_calls", None) else (message.role or "?")
        preview = (message.content or "").replace("\n", " ")[:100]
        print(f"  {kind}: {preview}")
    print("dsh_session_jsonl:")
    for path in jsonl_paths:
        print(f"  {path}")
    for name in ("AGENTS.md", "TOOLS.md"):
        path = workspace / name
        if path.is_file():
            print(f"{name.lower().replace('.', '_')}={path}")
    env_api = workspace / "ENV_API.txt"
    if env_api.is_file():
        print(f"env_api={env_api.read_text(encoding='utf-8').strip()}")
    print(f"policy_in_session_jsonl={outcome.policy_in_session_jsonl}")
    if needle:
        print(f"policy_needle={needle[:80]}")
    if outcome.first_request_tools:
        print(f"first_request_tools={outcome.first_request_tools}")
        mcp_names = [name for name in outcome.first_request_tools if name.startswith("mcp__tau2__")]
        print(f"first_request_mcp_tools={mcp_names}")
    if outcome.called_tools:
        print(f"called_tools={outcome.called_tools}")
    print(f"fail_reason={outcome.fail_reason}")


def _print_summary(all_outcomes: list[tuple[str, list[TaskOutcome]]]) -> None:
    """pass@1 table plus the most common fail_reason per layer."""
    print("summary:")
    print("layer\tdomain\ttask_id\treward\tdb\treason")
    for layer_name, outcomes in all_outcomes:
        passed = sum(1 for item in outcomes if item.passed)
        n = len(outcomes)
        print(f"# {layer_name} pass@1={passed}/{n}" + (f" ({passed / n:.2f})" if n else ""))
        for item in outcomes:
            print(
                f"{layer_name}\t{item.spec.domain}\t{item.spec.task_id}\t"
                f"{item.reward}\t{item.db_reward}\t{item.fail_reason}"
            )
        reasons = [item.fail_reason for item in outcomes if not item.passed]
        if reasons:
            counts: dict[str, int] = {}
            for reason in reasons:
                counts[reason] = counts.get(reason, 0) + 1
            top = ", ".join(f"{name}×{count}" for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
            print(f"# {layer_name} failures: {top}")
        mcp = sum(item.mcp_calls for item in outcomes)
        bash = sum(item.bash_calls for item in outcomes)
        compact = sum(item.compaction_events for item in outcomes)
        if mcp or bash or compact:
            print(f"# {layer_name} jsonl mcp={mcp} bash={bash} compaction={compact}")


def _write_summary(
    path: Path,
    all_outcomes: list[tuple[str, list[TaskOutcome]]],
    *,
    save_paths: list[str] | None = None,
    seed: int = SEED,
) -> None:
    """Write the pass@1 table as JSON next to the stage work tree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "seed": seed,
        "airline_task_ids": list(STAGE5_AIRLINE_TASK_IDS),
        "tau2_simulations": save_paths or [],
        "layers": [
            {
                "layer": layer_name,
                "pass_at_1": sum(1 for item in outcomes if item.passed) / len(outcomes) if outcomes else None,
                "passed": sum(1 for item in outcomes if item.passed),
                "n": len(outcomes),
                "mcp_calls": sum(item.mcp_calls for item in outcomes),
                "bash_calls": sum(item.bash_calls for item in outcomes),
                "compaction_events": sum(item.compaction_events for item in outcomes),
                "tasks": [
                    {
                        "domain": item.spec.domain,
                        "task_id": item.spec.task_id,
                        "reward": item.reward,
                        "db_reward": item.db_reward,
                        "reward_breakdown": item.reward_breakdown,
                        "termination": _jsonable(item.termination),
                        "fail_reason": item.fail_reason,
                        "called_tools": item.called_tools,
                        "first_request_tools": item.first_request_tools,
                        "mcp_calls": item.mcp_calls,
                        "bash_calls": item.bash_calls,
                        "compaction_events": item.compaction_events,
                        "tool_errors": item.tool_errors,
                        "policy_in_session_jsonl": item.policy_in_session_jsonl,
                        "error": item.error,
                        "workspace": item.workspace,
                    }
                    for item in outcomes
                ],
            }
            for layer_name, outcomes in all_outcomes
        ],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _jsonable(value: Any) -> Any:
    """JSON-serialize τ reward values (enums, floats)."""
    if hasattr(value, "value") and not isinstance(value, (int, float, str, bool)):
        return str(value)
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


def _jsonl_contains(path: Path, needle: str) -> bool:
    """True when any JSONL line includes needle as a substring."""
    if not needle:
        return False
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if needle in line:
                return True
    return False


def _first_request_tools(jsonl_paths: list[Path]) -> list[str]:
    """Tool names from the first request/header event, if present."""
    for path in jsonl_paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") != "request/header":
                    continue
                tools = event.get("data", {}).get("header", {}).get("tools") or []
                return [tool.get("name") for tool in tools if isinstance(tool, dict) and tool.get("name")]
    return []


def _called_tool_names(jsonl_paths: list[Path]) -> list[str]:
    """Model-issued tool names from tool/call (or equivalent) session events."""
    names: list[str] = []
    seen: set[str] = set()
    for path in jsonl_paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_type = event.get("type") or ""
                if event_type not in {"tool/call", "tool/request"}:
                    continue
                data = event.get("data") or {}
                name = data.get("name") or (data.get("message") or {}).get("name")
                if not isinstance(name, str) or name in seen:
                    continue
                seen.add(name)
                names.append(name)
    return names


if __name__ == "__main__":
    main()
