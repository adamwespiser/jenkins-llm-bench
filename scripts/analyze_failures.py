#!/usr/bin/env python3
"""
analyze_failures.py — Post-run failure analysis

For each failed task/condition, compares the agent's diff to the actual fix
and uses Claude to explain: what the agent tried, why it was wrong, and what
implicit knowledge was missing.

Usage:
    python3 analyze_failures.py [--results-dir DIR] [--output failures.csv]
"""

import argparse
import csv
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_DIR = Path.home() / "bench" / "repos" / "jenkins"
LOGS_DIR = Path.home() / "bench" / "logs"
TASKS_DIR = Path.home() / "bench" / "tasks"

sys.path.insert(0, str(
    Path.home() / "venvs" / "llm-bench" / "lib" / "python3.14" / "site-packages"
))
import anthropic


def git_diff(base, fixed, path, cwd):
    r = subprocess.run(
        f"git diff {base} {fixed} -- {path}",
        shell=True, cwd=cwd, capture_output=True, text=True,
    )
    return r.stdout.strip()


def find_agent_diff(task_id, condition, timestamp):
    pattern = f"{LOGS_DIR}/{task_id}__{condition}__*__{timestamp}__agent_diff.patch"
    matches = sorted(glob.glob(pattern))
    if matches:
        return Path(matches[-1]).read_text().strip()
    pattern2 = f"{LOGS_DIR}/{task_id}__{condition}__*__agent_diff.patch"
    matches2 = sorted(glob.glob(pattern2))
    if matches2:
        return Path(matches2[-1]).read_text().strip()
    return None  # Signal: need to reconstruct from agent log


def extract_edits_from_log(task_id, condition, timestamp):
    """Pull Edit/Write tool call blocks out of the agent log when no diff was saved."""
    pattern = f"{LOGS_DIR}/{task_id}__{condition}__*__{timestamp}__agent.log"
    matches = sorted(glob.glob(pattern))
    if not matches:
        pattern2 = f"{LOGS_DIR}/{task_id}__{condition}__*__agent.log"
        matches = sorted(glob.glob(pattern2))
    if not matches:
        return "(agent log not found)"

    log_text = Path(matches[-1]).read_text(errors="replace")
    # Extract lines containing Edit or Write tool use inputs
    edits = []
    for line in log_text.splitlines():
        if "name='Edit'" in line or "name='Write'" in line:
            # Trim to a readable length
            edits.append(line[:2000])
    if not edits:
        return "(no Edit/Write tool calls found in log)"
    return "\n---\n".join(edits[:30])  # cap at 30 edit blocks


def analyze_failure(task, result, actual_diff, agent_diff, client, diff_source=""):
    condition = result.get("condition", "")
    prompt_type = "full PR description + exact file path" if condition == "level1_full" \
        else "original bug report (no file hints)"

    prompt = f"""A coding agent was given a Jenkins bug to fix and failed. Analyze the failure.

## Task
Title: {task['title']}
Source file(s): {', '.join(task['source_files'])}
Condition: {condition} ({prompt_type})
Turns used: {result.get('agent', {}).get('num_messages', '?')}
Stop reason: {result.get('agent', {}).get('stop_reason', '?')}

## Bug description given to agent
{task.get('issue_text' if condition == 'level1_full' else 'issue_report', '')[:1500]}

## Actual fix (ground truth diff)
```diff
{actual_diff[:3000]}
```

## Agent's changes ({diff_source})
```
{agent_diff[:3000] if agent_diff else '(no changes made)'}
```

Answer these three questions in 2-3 sentences each:

1. WHAT_TRIED: What did the agent attempt? Describe the approach it took.
2. WHY_WRONG: Why was the agent's approach incorrect or insufficient?
3. MISSING_KNOWLEDGE: What implicit system knowledge or domain understanding would a Jenkins contributor have that the agent lacked? Be specific.

Reply as JSON with keys: what_tried, why_wrong, missing_knowledge"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    text = message.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except Exception:
        return {"what_tried": text, "why_wrong": "", "missing_knowledge": ""}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", default=str(Path.home() / "bench" / "results-issue-desc"))
    parser.add_argument("--output", default=str(Path.home() / "bench" / "failures.csv"))
    parser.add_argument("--conditions", nargs="+", default=["level1_full", "issue_report"])
    args = parser.parse_args()

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Load task metadata
    task_map = {}
    for tf in glob.glob(str(TASKS_DIR / "task_*.json")):
        t = json.load(open(tf))
        task_map[t["id"]] = t

    # Find failures
    failures = []
    for rf in sorted(glob.glob(f"{args.results_dir}/*.json")):
        d = json.load(open(rf))
        if d.get("outcome") in ("fail", "agent_error") and d.get("condition") in args.conditions:
            failures.append((rf, d))

    print(f"Analyzing {len(failures)} failures...")

    rows = []
    for rf, result in failures:
        task_id = result["task_id"]
        condition = result["condition"]
        timestamp = result["timestamp"]
        task = task_map.get(task_id, {})

        print(f"  {task_id} :: {condition} ...", end=" ", flush=True)

        actual_diff = git_diff(
            task.get("base_commit", "HEAD"),
            task.get("fixed_commit", "HEAD"),
            task["source_files"][0] if task.get("source_files") else ".",
            REPO_DIR,
        )

        agent_diff = find_agent_diff(task_id, condition, timestamp)
        if agent_diff is None:
            agent_diff = extract_edits_from_log(task_id, condition, timestamp)
            diff_source = "reconstructed from agent log (Edit/Write tool calls)"
        else:
            diff_source = "saved diff"

        analysis = analyze_failure(task, result, actual_diff, agent_diff, client, diff_source)
        print("done")

        rows.append({
            "task_id": task_id,
            "condition": condition,
            "outcome": result.get("outcome"),
            "source_files": len(task.get("source_files", [])),
            "turns": result.get("agent", {}).get("num_messages", ""),
            "cost_usd": round(result.get("agent", {}).get("cost_usd", 0), 4),
            "stop_reason": result.get("agent", {}).get("stop_reason", ""),
            "diff_source": diff_source,
            "agent_made_changes": "yes" if agent_diff and "not found" not in agent_diff else "no",
            "what_tried": analysis.get("what_tried", ""),
            "why_wrong": analysis.get("why_wrong", ""),
            "missing_knowledge": analysis.get("missing_knowledge", ""),
        })

    out = Path(args.output)
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
