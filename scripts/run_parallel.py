#!/usr/bin/env python3
"""
run_parallel.py — Parallel benchmark runner

Usage:
    python3 run_parallel.py [options]

Options:
    --tasks-file FILE      JSON list of task file paths (default: selected_21.json)
    --conditions C [C...]  Conditions to run (default: issue_report level1_full)
    --results-dir DIR      Output directory (default: ~/bench/results-issue-desc)
    --model MODEL          Model slug (default: claude-sonnet-4-6)
    --max-turns N          Max agent turns (default: 40)
    --budget B             Max cost per run in USD (default: 2.0)
    --workers N            Parallel workers (default: 8)
    --skip-existing        Skip task/condition pairs with a passing result already
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BENCH = Path.home() / "bench"
SCRIPT = BENCH / "scripts" / "run_task.py"
VENV_PYTHON = Path.home() / "venvs" / "llm-bench" / "bin" / "python3"
PYTHON = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def ts():
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def log(msg):
    print(f"[{ts()}] {msg}", flush=True)


def load_existing_passes(results_dir):
    passed = set()
    for f in Path(results_dir).glob("*.json"):
        try:
            d = json.loads(f.read_text())
            if d.get("outcome") == "pass":
                passed.add((d["task_id"], d.get("condition", "")))
        except Exception:
            pass
    return passed


async def run_one(task_file, condition, model, max_turns, budget, results_dir, sem, counters, total):
    async with sem:
        task_id = json.loads(Path(task_file).read_text())["id"]
        label = f"{task_id} :: {condition}"

        cmd = [
            PYTHON, str(SCRIPT), task_file,
            "--condition", condition,
            "--model", model,
            "--max-turns", str(max_turns),
            "--budget", str(budget),
            "--results-dir", str(results_dir),
        ]

        env = {**os.environ}

        log(f"START {label}")
        start = time.time()
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        stdout, _ = await proc.communicate()
        elapsed = time.time() - start
        rc = proc.returncode

        counters["done"] += 1
        n = counters["done"]

        if rc == 0:
            counters["pass"] += 1
            outcome = "PASS"
        elif rc == 1:
            counters["fail"] += 1
            outcome = "FAIL"
        else:
            counters["error"] += 1
            outcome = "ERROR"

        # Print last meaningful line from output
        lines = [l for l in stdout.decode(errors="replace").splitlines() if l.strip()]
        summary = lines[-1] if lines else "(no output)"
        log(f"[{n}/{total}] {outcome} {label} ({elapsed:.0f}s) — {summary}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks-file", default=str(BENCH / "tasks" / "selected_21.json"))
    parser.add_argument("--conditions", nargs="+", default=["issue_report", "level1_full"])
    parser.add_argument("--results-dir", default=str(BENCH / "results-issue-desc"))
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument("--budget", type=float, default=2.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    task_files = json.loads(Path(args.tasks_file).read_text())
    existing = load_existing_passes(results_dir) if args.skip_existing else set()

    jobs = []
    for tf in task_files:
        task_id = json.loads(Path(tf).read_text())["id"]
        for cond in args.conditions:
            if (task_id, cond) in existing:
                log(f"SKIP {task_id} :: {cond} (already passed)")
                continue
            jobs.append((tf, cond))

    total = len(jobs)
    log(f"{total} runs | {args.workers} workers | model={args.model} max_turns={args.max_turns} budget=${args.budget}")
    log(f"Conditions: {args.conditions}")
    log(f"Results dir: {results_dir}")
    log(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print()

    sem = asyncio.Semaphore(args.workers)
    counters = {"done": 0, "pass": 0, "fail": 0, "error": 0}

    tasks = [
        run_one(tf, cond, args.model, args.max_turns, args.budget, results_dir, sem, counters, total)
        for tf, cond in jobs
    ]
    await asyncio.gather(*tasks)

    print()
    log(f"Done. Pass={counters['pass']} Fail={counters['fail']} Error={counters['error']} Total={total}")


if __name__ == "__main__":
    asyncio.run(main())
