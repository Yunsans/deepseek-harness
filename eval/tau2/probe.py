#!/usr/bin/env python3
"""Stage 0: two turns on one session against the current checkout's JSON-RPC runtime.

Run from the deepseek-harness repository root (see eval/tau2/plan.md).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from deepseek_harness import DeepSeekHarness

REPO_ROOT = Path(__file__).resolve().parents[2]
CORDIS = REPO_ROOT / "examples/jsonrpc-agent/minimal.cordis.yml"
RUNTIME_BIN = REPO_ROOT / "packages/examples/jsonrpc-demo/src/bin.ts"
WORK = Path(__file__).resolve().parent / ".work" / "stage0"
SESSION_ID = "probe-1"


def main() -> None:
    """Start the source runtime, run two prompts on one session, print the JSONL path."""
    if not (REPO_ROOT / "pnpm-workspace.yaml").is_file():
        sys.exit(f"expected repository root at {REPO_ROOT}")
    if not os.environ.get("DEEPSEEK_API_KEY"):
        sys.exit("set DEEPSEEK_API_KEY (repo-root .env is not loaded by this script)")

    workspace = WORK / "workspace"
    session_root = WORK / "sessions"
    workspace.mkdir(parents=True, exist_ok=True)
    session_root.mkdir(parents=True, exist_ok=True)

    with DeepSeekHarness(
        provider="deepseek-official",
        model=os.environ.get("DSH_MODEL", "deepseek-v4-flash"),
        cwd=str(workspace),
        runtime_cwd=str(REPO_ROOT),
        session_root=str(session_root),
        cordis=str(CORDIS),
        launch_args_override=("node", "--import", "tsx", str(RUNTIME_BIN)),
    ) as harness:
        first = harness.run("Reply with exactly this word and nothing else: ready", session_id=SESSION_ID)
        print("turn1:", first.final_response)
        second = harness.run("What exact word did you reply with in the previous turn?", session_id=SESSION_ID)
        print("turn2:", second.final_response)

    jsonl = sorted(session_root.rglob("*.jsonl"))
    if not jsonl:
        sys.exit(f"no JSONL under {session_root}")
    print("session_jsonl:")
    for path in jsonl:
        print(f"  {path}")


if __name__ == "__main__":
    main()
