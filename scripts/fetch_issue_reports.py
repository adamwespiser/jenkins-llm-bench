#!/usr/bin/env python3
"""
fetch_issue_reports.py — Fetch original bug report text for each task.

For tasks with a GitHub issue link: uses `gh issue view`.
For tasks with a JENKINS- Jira ref: uses the public issues.jenkins.io REST API.
Adds `issue_report` and `issue_report_source` fields to each task JSON in-place.

Usage:
    python3 fetch_issue_reports.py [--tasks-file selected_30.json] [--dry-run]
"""

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

import urllib.request
import urllib.error

TASKS_DIR = Path.home() / "bench" / "tasks"
JIRA_API = "https://issues.jenkins.io/rest/api/2/issue/{}"


def fetch_github_issue(repo, number):
    result = subprocess.run(
        ["gh", "issue", "view", str(number), "--repo", repo, "--json", "title,body"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh issue view failed: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    title = data.get("title", "")
    body = data.get("body", "")
    return f"## {title}\n\n{body}".strip()


def fetch_jira_issue(jira_id):
    url = JIRA_API.format(jira_id)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Jira HTTP {e.code} for {jira_id}")
    fields = data.get("fields", {})
    summary = fields.get("summary", "")
    description = fields.get("description") or ""
    return f"## {summary}\n\n{description}".strip()


def extract_refs(task):
    combined = task.get("title", "") + " " + task.get("issue_text", "")
    jira = re.findall(r"JENKINS-\d+", combined)
    gh_issues = re.findall(r"github\.com/jenkinsci/jenkins/issues/(\d+)", combined)
    fixes = re.findall(r"[Ff]ixes\s+#(\d+)", combined)
    return jira, gh_issues or fixes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks-file", default=str(TASKS_DIR / "selected_30.json"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    task_files = json.loads(Path(args.tasks_file).read_text())

    ok = skipped = failed = 0

    for tf in task_files:
        path = Path(tf)
        task = json.loads(path.read_text())
        tid = task["id"]

        if task.get("issue_report"):
            print(f"  SKIP  {tid} (already has issue_report)")
            skipped += 1
            continue

        jira_refs, gh_refs = extract_refs(task)

        source = None
        text = None

        if gh_refs:
            number = gh_refs[0]
            source = f"github:jenkinsci/jenkins#{number}"
            try:
                text = fetch_github_issue("jenkinsci/jenkins", number)
                print(f"  OK    {tid}  ← GitHub #{number}")
            except Exception as e:
                print(f"  FAIL  {tid}  GitHub #{number}: {e}")
                failed += 1
                continue

        elif jira_refs:
            jira_id = jira_refs[0]
            source = f"jira:{jira_id}"
            try:
                text = fetch_jira_issue(jira_id)
                print(f"  OK    {tid}  ← Jira {jira_id}")
            except Exception as e:
                print(f"  FAIL  {tid}  Jira {jira_id}: {e}")
                failed += 1
                continue

        else:
            print(f"  NONE  {tid} (no GitHub issue or Jira ref found)")
            skipped += 1
            continue

        if not args.dry_run:
            task["issue_report"] = text
            task["issue_report_source"] = source
            path.write_text(json.dumps(task, indent=2))

        ok += 1
        time.sleep(0.3)  # be polite to APIs

    print(f"\nDone: {ok} fetched, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()
