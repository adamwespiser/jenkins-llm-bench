#!/usr/bin/env python3
"""
validate_tasks.py — Run base validation for all selected tasks.

For each task: create worktree at base_commit, inject test files, run test.
If test PASSES on base, the task is misconfigured (test doesn't reproduce the bug).
Prints a summary and writes a filtered selected list.
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_DIR = Path.home() / "bench" / "repos" / "jenkins"
WORKTREE_DIR = Path.home() / "bench-worktrees"
TASKS_FILE = Path.home() / "bench" / "tasks" / "selected_30.json"
OUT_FILE = Path.home() / "bench" / "tasks" / "selected_validated.json"


def run(cmd, cwd=None, timeout=300):
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def validate_task(task_file):
    task = json.loads(Path(task_file).read_text())
    task_id = task["id"]
    worktree = WORKTREE_DIR / f"val__{task_id}"

    try:
        # Setup worktree
        if worktree.exists():
            run(f"git worktree remove --force {worktree}", cwd=REPO_DIR)
            shutil.rmtree(worktree, ignore_errors=True)
        code, _, err = run(f"git worktree add {worktree} {task['base_commit']}", cwd=REPO_DIR)
        if code != 0:
            return task_id, "error", f"worktree failed: {err[:100]}"

        # Inject test files
        for rel_path in task.get("test_files_injected", []):
            dest = worktree / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            code, content, err = run(f"git show {task['fixed_commit']}:{rel_path}", cwd=REPO_DIR)
            if code != 0:
                return task_id, "error", f"injection failed: {err[:100]}"
            dest.write_text(content)

        # Run test
        start = time.time()
        code, stdout, stderr = run(task["test_command"], cwd=worktree, timeout=300)
        elapsed = time.time() - start
        passed = code == 0
        status = "PASS_bad" if passed else "FAIL_good"
        return task_id, status, f"{elapsed:.1f}s"

    except Exception as e:
        return task_id, "error", str(e)[:100]

    finally:
        run(f"git worktree remove --force {worktree}", cwd=REPO_DIR)
        shutil.rmtree(worktree, ignore_errors=True)


def main():
    task_files = json.loads(TASKS_FILE.read_text())
    print(f"Validating {len(task_files)} tasks...")
    print(f"{'ID':<40} {'status':<12} {'detail'}")
    print("-" * 80)

    good, bad, error = [], [], []
    for tf in task_files:
        task_id, status, detail = validate_task(tf)
        print(f"{task_id:<40} {status:<12} {detail}")
        sys.stdout.flush()
        if status == "FAIL_good":
            good.append(tf)
        elif status == "PASS_bad":
            bad.append(tf)
        else:
            error.append(tf)

    print()
    print(f"Good (test fails on base):  {len(good)}")
    print(f"Bad (test passes on base):  {len(bad)}")
    print(f"Error (could not validate): {len(error)}")

    OUT_FILE.write_text(json.dumps(good, indent=2))
    print(f"\nSaved {len(good)} validated tasks to {OUT_FILE}")

    if bad:
        print("\nMisconfigured tasks (test passes on base — should be replaced):")
        for tf in bad:
            print(f"  {Path(tf).stem}")
    if error:
        print("\nError tasks:")
        for tf in error:
            print(f"  {Path(tf).stem}")


if __name__ == "__main__":
    main()
