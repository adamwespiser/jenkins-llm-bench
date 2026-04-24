#!/usr/bin/env python3
"""
mine_tasks.py — Extract benchmark tasks from Jenkins git history

Two passes over git log:
  Pass 1 (--diff-filter=A): commits that ADDED a new Test*.java alongside source changes
  Pass 2 (--diff-filter=M): commits that MODIFIED a Test*.java and added new @Test methods

For each qualifying commit:
  - Look up its PR via GitHub API (fall back to commit message for direct commits)
  - Apply size filters (additions/deletions < 500)
  - Emit a task JSON

Date range is configurable via --after (default: 2024-01-01).
"""

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = "jenkinsci/jenkins"
TASKS_DIR = Path.home() / "bench" / "tasks"
REPO_DIR = Path.home() / "bench" / "repos" / "jenkins"

TEST_FILE_RE = re.compile(r"Test\.java$|Spec\.java$")
SOURCE_JAVA_RE = re.compile(r"\.java$")
NEW_TEST_ANNOTATION_RE = re.compile(r"^\+\s*@Test")

MAX_ADDITIONS = 500
MAX_DELETIONS = 500


# ---------------------------------------------------------------------------
# Git / GitHub helpers
# ---------------------------------------------------------------------------

def git(cmd, cwd=None):
    r = subprocess.run(["git"] + cmd, capture_output=True, text=True,
                       cwd=str(cwd or REPO_DIR))
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def gh_json(cmd):
    r = subprocess.run(["gh"] + cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:200])
    return json.loads(r.stdout)


def find_pr_for_commit(sha):
    r = subprocess.run(
        ["gh", "api", f"repos/{REPO}/commits/{sha}/pulls", "--jq", ".[0].number"],
        capture_output=True, text=True,
    )
    if r.returncode == 0 and r.stdout.strip().isdigit():
        return int(r.stdout.strip())
    return None


def commit_metadata(sha):
    """Return (subject, body, author_date) from the commit itself."""
    _, out, _ = git(["log", "-1", "--pretty=format:%s%n%n%b%n----%n%ai", sha])
    parts = out.split("----\n", 1)
    message = parts[0].strip()
    date = parts[1].strip()[:10] if len(parts) > 1 else "2000-01-01"
    lines = message.splitlines()
    subject = lines[0].strip() if lines else sha[:12]
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
    return subject, body, date


def files_in_commit(sha):
    """Return {status -> [paths]} for all files changed in a commit."""
    _, ns, _ = git(["diff-tree", "--no-commit-id", "-r", "--name-status", sha])
    result = {}
    for line in ns.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            status, path = parts
            result.setdefault(status[0], []).append(path)
    return result


def numstat_totals(sha):
    _, out, _ = git(["diff-tree", "--no-commit-id", "-r", "--numstat", sha])
    add, delete = 0, 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            add += int(parts[0])
            delete += int(parts[1])
    return add, delete


# ---------------------------------------------------------------------------
# Two git-log passes
# ---------------------------------------------------------------------------

def pass1_new_test_files(after):
    """Commits that ADDED a new Test*.java alongside at least one source file."""
    _, output, _ = git([
        "log", f"--after={after}", "--diff-filter=A",
        "--pretty=format:%H", "--", "*Test.java", "*Spec.java",
    ])
    results = []
    for sha in (s.strip() for s in output.splitlines() if s.strip()):
        changed = files_in_commit(sha)
        added_tests = [p for p in changed.get("A", []) if TEST_FILE_RE.search(p)]
        source_files = [
            p for status in ("A", "M")
            for p in changed.get(status, [])
            if SOURCE_JAVA_RE.search(p) and not TEST_FILE_RE.search(p)
        ]
        if added_tests and source_files:
            results.append({
                "sha": sha,
                "signal": "new_file",
                "added_tests": added_tests,
                "source_files": source_files,
            })
    return results


def pass2_modified_test_files(after):
    """Commits that MODIFIED a Test*.java and added new @Test methods + touched source."""
    _, output, _ = git([
        "log", f"--after={after}", "--diff-filter=M",
        "--pretty=format:%H", "--", "*Test.java", "*Spec.java",
    ])
    results = []
    for sha in (s.strip() for s in output.splitlines() if s.strip()):
        changed = files_in_commit(sha)
        source_files = [
            p for p in changed.get("M", [])
            if SOURCE_JAVA_RE.search(p) and not TEST_FILE_RE.search(p)
        ]
        if not source_files:
            continue

        # Check each modified test file for newly added @Test methods
        tests_with_new_methods = []
        for tf in changed.get("M", []):
            if not TEST_FILE_RE.search(tf):
                continue
            _, patch, _ = git(["diff", f"{sha}^", sha, "--", tf])
            if any(NEW_TEST_ANNOTATION_RE.match(l) for l in patch.splitlines()):
                tests_with_new_methods.append(tf)

        if tests_with_new_methods:
            results.append({
                "sha": sha,
                "signal": "modified_with_new_methods",
                "added_tests": tests_with_new_methods,
                "source_files": source_files,
            })
    return results


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def maven_module_for(test_path):
    module = Path(test_path).parts[0] if Path(test_path).parts else "core"
    return module, module == "test"


def build_test_command(module, test_class):
    return (
        f"mvn test -pl {module} -am -Dtest={test_class} "
        f"-DfailIfNoTests=false --no-transfer-progress -Denforcer.skip=true -q"
    )


def classify(commit_info, cutoff_date):
    sha = commit_info["sha"]
    pr_number = find_pr_for_commit(sha)

    if pr_number:
        try:
            pr = gh_json(["pr", "view", str(pr_number), "--repo", REPO,
                          "--json", "title,body,mergeCommit,additions,deletions,mergedAt"])
        except RuntimeError as e:
            return None, f"gh error: {e}"
        additions = pr.get("additions", 0)
        deletions = pr.get("deletions", 0)
        merged_at = pr.get("mergedAt", "")
        merge_sha = (pr.get("mergeCommit") or {}).get("oid")
        title = pr["title"]
        issue_text = (pr.get("body") or "").strip()[:2000]
        task_id = f"jenkins-{pr_number}"
    else:
        additions, deletions = numstat_totals(sha)
        title, issue_text, merged_at = commit_metadata(sha)
        merge_sha = sha
        task_id = f"jenkins-commit-{sha[:10]}"

    if additions >= MAX_ADDITIONS:
        return None, f"too many additions ({additions})"
    if deletions >= MAX_DELETIONS:
        return None, f"too many deletions ({deletions})"
    if merged_at < cutoff_date:
        return None, f"too old ({merged_at[:10]})"

    _, base_commit, _ = git(["rev-parse", f"{merge_sha}^1"])
    if not base_commit:
        return None, "could not resolve base commit"

    test_file = commit_info["added_tests"][0]
    module, is_integration = maven_module_for(test_file)
    test_class = Path(test_file).stem

    return {
        "id": task_id,
        "pr": pr_number,
        "title": title,
        "merged_at": merged_at[:10],
        "issue_text": issue_text or title,
        "base_commit": base_commit[:10],
        "fixed_commit": merge_sha[:10],
        "additions": additions,
        "deletions": deletions,
        "source_files": commit_info["source_files"],
        "test_files_injected": commit_info["added_tests"],
        "test_injection_type": commit_info["signal"],
        "maven_module": module,
        "test_class": test_class,
        "test_command": build_test_command(module, test_class),
        "is_integration_test": is_integration,
        "expected_fail_on_base": True,
        "expected_pass_on_fixed": True,
        "difficulty": "medium" if len(commit_info["source_files"]) <= 3 else "hard",
        "notes": (
            f"{len(commit_info['source_files'])} source file(s), signal={commit_info['signal']}. "
            f"{'Integration test.' if is_integration else 'Unit test.'}"
            f"{' No public PR.' if not pr_number else ''}"
        ),
    }, None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--after", default="2024-01-01",
                        help="Only include commits after this date (default: 2024-01-01)")
    parser.add_argument("--output-dir", default=str(TASKS_DIR))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-integration", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Pass 1: new test files added after {args.after} ...")
    p1 = pass1_new_test_files(args.after)
    print(f"  {len(p1)} commits")

    print(f"Pass 2: modified test files with new @Test methods after {args.after} ...")
    p2 = pass2_modified_test_files(args.after)
    print(f"  {len(p2)} commits")

    all_commits = p1 + p2
    print(f"Total: {len(all_commits)} candidate commits — classifying ...")

    tasks, skipped, seen_prs = [], {}, set()

    for c in all_commits:
        label = c["added_tests"][0].split("/")[-1]
        print(f"  {c['sha'][:8]} [{c['signal'][:3]}] ({label}) ...", end=" ", flush=True)
        task, reason = classify(c, args.after)
        if not task:
            skipped.setdefault(reason, []).append(c["sha"])
            print(f"skip ({reason})")
            continue
        dedup_key = task["pr"] or task["id"]
        if dedup_key in seen_prs:
            print("skip (duplicate)")
            continue
        if task["is_integration_test"] and not args.include_integration:
            reason = "integration test (use --include-integration)"
            skipped.setdefault(reason, []).append(c["sha"])
            print(f"skip ({reason})")
            continue
        seen_prs.add(dedup_key)
        print(f"OK → {task['id']}  [{task['difficulty']}]  {task['merged_at']}")
        tasks.append(task)

    print(f"\nFound {len(tasks)} tasks, skipped {sum(len(v) for v in skipped.values())}")
    for reason, items in sorted(skipped.items(), key=lambda x: -len(x[1])):
        print(f"  {len(items):3d} × {reason}")

    if not args.dry_run:
        for task in tasks:
            path = output_dir / f"task_{task['id']}.json"
            path.write_text(json.dumps(task, indent=2))
            print(f"  Saved: {path}")


if __name__ == "__main__":
    main()
