#!/usr/bin/env python3
"""
run_task.py — Jenkins LLM Benchmark Harness

Conditions (--condition):
  level1_full    Prompt names the exact source file(s); agent works in full repo
  level2_full    Prompt gives only bug description + test command; full repo
  level2_module  Same as level2_full but agent cwd is the relevant Maven module only

For each condition:
  1. Create git worktree at base commit
  2. Inject test file(s)
  3. Validate: test FAILS on base
  4. Run agent
  5. Validate: test PASSES after fix
  6. Write results JSON
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_DIR = Path.home() / "bench" / "repos" / "jenkins"
WORKTREE_DIR = Path.home() / "bench-worktrees"
DEFAULT_RESULTS_DIR = Path.home() / "bench" / "results"
LOGS_DIR = Path.home() / "bench" / "logs"

CONDITIONS = ("level1_full", "level2_full", "level2_module", "issue_report")


def run(cmd, cwd=None, timeout=600, capture=True):
    result = subprocess.run(
        cmd, shell=True, cwd=cwd,
        capture_output=capture, text=True, timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

def build_prompt(task, condition):
    if condition == "level1_full":
        return f"""You are a Jenkins contributor tasked with fixing a bug.

Bug description:
{task['issue_text']}

The following source file(s) contain the bug:
  {chr(10).join("  " + f for f in task['source_files'])}

A failing test has been written to reproduce the bug. Run it with:
  {task['test_command']}

Steps:
1. Read the source file(s) listed above.
2. Read the failing test to understand what behavior is expected.
3. Fix the production code.
4. Run the test command to confirm all tests pass.
5. Iterate if needed.

Do not modify the test file.
"""
    elif condition == "issue_report":
        return f"""You are a Jenkins contributor. A bug has been reported and a failing test has been written to reproduce it. The test file is already present in the repository.

Bug report:
{task['issue_report']}

Run this command to see the failing test:
  {task['test_command']}

Your working directory is the root of the Jenkins source repository. All file operations should use relative paths from this directory.

Your task:
1. Run the failing test to see what's broken.
2. Read the test to understand what behavior is expected.
3. Search the codebase to find the production code responsible for the bug.
4. Fix the production code.
5. Re-run the test to confirm all tests pass.

Do not modify the test file. Fix only production code.
Do not run `mvn install` or any full-repo build — only use the test command above.
Do not use absolute paths — use relative paths from the current working directory.
"""
    else:  # level2_full or level2_module
        scope_note = (
            "" if condition == "level2_full"
            else f"\nThe relevant code is within the `{task['maven_module']}` module.\n"
        )
        return f"""You are a Jenkins contributor. A bug has been reported and a failing test has been written to reproduce it. The test file is already present in the repository.

Bug description:
{task['issue_text']}
{scope_note}
Run this command to see the failing test:
  {task['test_command']}

Your task:
1. Run the failing test to see what's broken.
2. Read the test to understand what behavior is expected.
3. Search the codebase to find the production code responsible for the bug.
4. Fix the production code.
5. Re-run the test to confirm all tests pass.

Do not modify the test file. Fix only production code.
"""


# ---------------------------------------------------------------------------
# Worktree / test helpers
# ---------------------------------------------------------------------------

def setup_worktree(task, worktree_path):
    if worktree_path.exists():
        run(f"git worktree remove --force {worktree_path}", cwd=REPO_DIR)
        shutil.rmtree(worktree_path, ignore_errors=True)

    log(f"Creating worktree at {task['base_commit'][:8]}")
    code, _, err = run(
        f"git worktree add {worktree_path} {task['base_commit']}",
        cwd=REPO_DIR,
    )
    if code != 0:
        raise RuntimeError(f"Failed to create worktree:\n{err}")


def inject_test_files(task, worktree_path):
    for rel_path in task.get("test_files_injected", []):
        dest = worktree_path / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        code, content, err = run(
            f"git show {task['fixed_commit']}:{rel_path}", cwd=REPO_DIR,
        )
        if code != 0:
            raise RuntimeError(f"Could not get {rel_path} from fixed commit:\n{err}")
        dest.write_text(content)
        log(f"Injected {rel_path}")



def run_test(cwd, test_command, label, log_path):
    log(f"Running test [{label}] ...")
    start = time.time()
    code, stdout, stderr = run(test_command, cwd=cwd, timeout=300)
    duration = time.time() - start
    log_path.write_text(stdout + "\n" + stderr)
    passed = code == 0
    log(f"  → {'PASS' if passed else 'FAIL'} in {duration:.1f}s (exit {code})")
    return passed, duration


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

async def run_agent(prompt, agent_cwd, model, max_turns, budget_usd, log_path):
    sys.path.insert(0, str(
        Path.home() / "venvs" / "llm-bench" / "lib" / "python3.14" / "site-packages"
    ))
    from claude_agent_sdk import query, ClaudeAgentOptions, ClaudeSDKError
    from claude_agent_sdk.types import ResultMessage

    options = ClaudeAgentOptions(
        model=model,
        cwd=str(agent_cwd),
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        max_budget_usd=budget_usd,
        allowed_tools=["Read", "Edit", "Write", "Bash", "Glob", "Grep"],
        env={"ANTHROPIC_API_KEY": v} if (v := os.environ.get("ANTHROPIC_API_KEY")) else {},
    )

    log(f"Starting agent ({model}, cwd={Path(agent_cwd).name}, max_turns={max_turns})")
    start = time.time()
    messages, result_msg = [], None
    total_cost = 0.0
    input_tokens = output_tokens = 0

    with open(log_path, "w") as f:
        f.write(f"PROMPT:\n{prompt}\n\n{'='*60}\n\n")
        try:
            async for msg in query(prompt=prompt, options=options):
                messages.append(msg)
                f.write(repr(msg) + "\n")
                if isinstance(msg, ResultMessage):
                    result_msg = msg
                    if isinstance(msg.usage, dict):
                        input_tokens = msg.usage.get("input_tokens", 0)
                        output_tokens = msg.usage.get("output_tokens", 0)
                    if msg.total_cost_usd:
                        total_cost = msg.total_cost_usd
        except Exception as e:
            f.write(f"\nSDK error (possibly max_turns exit): {e}\n")
            if result_msg is None:
                raise

    duration = time.time() - start
    log(f"Agent done in {duration:.1f}s | cost=${total_cost:.4f}")
    return {
        "duration_s": duration,
        "cost_usd": total_cost,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "num_messages": len(messages),
        "stop_reason": result_msg.stop_reason if result_msg else None,
        "is_error": result_msg.is_error if result_msg else True,
    }


# ---------------------------------------------------------------------------
# Change counting
# ---------------------------------------------------------------------------

def count_changes(worktree_path, task):
    changed_files = changed_lines = 0
    for rel_path in task["source_files"]:
        _, diff, _ = run(f"git diff HEAD -- {rel_path}", cwd=worktree_path)
        if diff.strip():
            changed_files += 1
            changed_lines += sum(
                1 for l in diff.splitlines()
                if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))
            )
    return changed_files, changed_lines


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task_file")
    parser.add_argument("--condition", choices=CONDITIONS, required=True)
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--max-turns", type=int, default=20)
    parser.add_argument("--budget", type=float, default=2.0)
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR))
    parser.add_argument("--skip-base-validation", action="store_true")
    parser.add_argument("--keep-worktree", action="store_true")
    args = parser.parse_args()

    task = json.loads(Path(args.task_file).read_text())
    task_id = task["id"]
    condition = args.condition
    results_dir = Path(args.results_dir)

    if condition == "issue_report" and not task.get("issue_report"):
        print(f"ERROR: task {task_id} has no issue_report field — skipping")
        return 2

    for d in (WORKTREE_DIR, results_dir, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    worktree_path = WORKTREE_DIR / f"{task_id}__{condition}"
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    slug = args.model.replace("/", "_")
    log_prefix = LOGS_DIR / f"{task_id}__{condition}__{slug}__{run_ts}"

    # Determine agent cwd and test cwd
    # All conditions run from the worktree root — the CLI requires a git root as cwd.
    # level2_module scopes the search via the prompt, not by changing cwd.
    agent_cwd = worktree_path
    test_cmd = task["test_command"]

    result = {
        "task_id": task_id,
        "condition": condition,
        "model": args.model,
        "timestamp": run_ts,
        "base_commit": task["base_commit"],
        "fixed_commit": task["fixed_commit"],
        "base_test_passed": None,
        "post_agent_test_passed": None,
        "agent": {},
        "files_changed": 0,
        "lines_changed": 0,
        "outcome": "unknown",
        "error": None,
    }

    try:
        setup_worktree(task, worktree_path)
        inject_test_files(task, worktree_path)

        if not args.skip_base_validation:
            base_passed, base_dur = run_test(
                worktree_path, task["test_command"], "base",
                Path(f"{log_prefix}__base.log"),
            )
            result["base_test_passed"] = base_passed
            result["base_test_duration_s"] = base_dur
            if base_passed and task.get("expected_fail_on_base"):
                log("WARNING: test passed on base — task may be misconfigured")

        prompt = build_prompt(task, condition)
        agent_result = asyncio.run(run_agent(
            prompt, agent_cwd, args.model, args.max_turns, args.budget,
            Path(f"{log_prefix}__agent.log"),
        ))
        result["agent"] = agent_result
        result["files_changed"], result["lines_changed"] = count_changes(worktree_path, task)

        # Save agent's diff before worktree is cleaned up
        _, diff_out, _ = run("git diff HEAD", cwd=worktree_path)
        diff_path = Path(f"{log_prefix}__agent_diff.patch")
        diff_path.write_text(diff_out)

        post_passed, post_dur = run_test(
            worktree_path, task["test_command"], "post-agent",
            Path(f"{log_prefix}__post.log"),
        )
        result["post_agent_test_passed"] = post_passed
        result["post_agent_test_duration_s"] = post_dur
        result["outcome"] = (
            "pass" if post_passed
            else "agent_error" if agent_result.get("is_error")
            else "fail"
        )

    except Exception as e:
        result["outcome"] = "harness_error"
        result["error"] = str(e)
        log(f"ERROR: {e}")
        import traceback; traceback.print_exc()

    finally:
        if not args.keep_worktree:
            run(f"git worktree remove --force {worktree_path}", cwd=REPO_DIR)
            shutil.rmtree(worktree_path, ignore_errors=True)

    result_file = results_dir / f"{task_id}__{condition}__{slug}__{run_ts}.json"
    result_file.write_text(json.dumps(result, indent=2))
    log(f"\nResult: {result['outcome'].upper()} — {result_file.name}")
    return 0 if result["outcome"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
