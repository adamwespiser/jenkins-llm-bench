#!/usr/bin/env python3
"""
select_tasks.py — Pick 30 benchmark tasks from the mined pool.

Selection strategy:
  - Prefer tasks with a real PR number (richer issue_text)
  - Prefer unit tests (faster to run) but include some integration tests
  - Spread evenly across years (2024 / 2025 / 2026)
  - Mix of difficulty (medium / hard)
  - Deduplicate by test class name (don't run the same test twice)
"""

import json
import re
import sys
from pathlib import Path

TASKS_DIR = Path.home() / "bench" / "tasks"
TARGET = 30


BAD_ISSUE_TEXT_RE = re.compile(
    r"^(This PR contains the following updates|<!--\s*Comment:)",
    re.IGNORECASE,
)


def has_usable_issue_text(task):
    text = (task.get("issue_text") or "").strip()
    if len(text) < 40:
        return False
    if BAD_ISSUE_TEXT_RE.match(text):
        return False
    return True


def score(task):
    """Higher score = higher priority for selection."""
    s = 0
    if task.get("pr") is not None:
        s += 10                          # real PR preferred over direct commit
    if not task.get("is_integration_test"):
        s += 5                           # unit tests preferred (faster)
    if task.get("difficulty") == "hard":
        s += 2                           # keep some hard ones
    year = (task.get("merged_at") or "2024")[:4]
    s += {"2026": 3, "2025": 2, "2024": 1}.get(year, 0)  # recency bonus
    return s


def main():
    all_tasks = []
    for f in sorted(TASKS_DIR.glob("task_jenkins-*.json")):
        try:
            t = json.loads(f.read_text())
            t["_file"] = str(f)
            all_tasks.append(t)
        except Exception:
            pass

    print(f"Loaded {len(all_tasks)} tasks")

    # Drop tasks with bad issue text before scoring
    before = len(all_tasks)
    all_tasks = [t for t in all_tasks if has_usable_issue_text(t)]
    print(f"Dropped {before - len(all_tasks)} tasks with bad issue text, {len(all_tasks)} remain")

    # Sort by score descending
    all_tasks.sort(key=score, reverse=True)

    selected = []
    seen_test_classes = set()
    year_counts = {"2024": 0, "2025": 0, "2026": 0}
    max_per_year = 12   # rough cap to spread across years

    for task in all_tasks:
        if len(selected) >= TARGET:
            break
        tc = task.get("test_class", "")
        if tc in seen_test_classes:
            continue
        year = (task.get("merged_at") or "2024")[:4]
        if year_counts.get(year, 0) >= max_per_year:
            continue
        seen_test_classes.add(tc)
        year_counts[year] = year_counts.get(year, 0) + 1
        selected.append(task)

    # If we haven't hit 30 yet, fill without the year cap
    if len(selected) < TARGET:
        for task in all_tasks:
            if len(selected) >= TARGET:
                break
            tc = task.get("test_class", "")
            if tc in seen_test_classes:
                continue
            seen_test_classes.add(tc)
            selected.append(task)

    print(f"\nSelected {len(selected)} tasks:")
    print(f"{'ID':<40} {'date':<12} {'type':<12} {'diff':<8} {'pr?'}")
    print("-" * 85)
    for t in selected:
        kind = "integration" if t.get("is_integration_test") else "unit"
        has_pr = "yes" if t.get("pr") else "no"
        print(f"{t['id']:<40} {t.get('merged_at','?'):<12} {kind:<12} {t.get('difficulty','?'):<8} {has_pr}")

    year_dist = {}
    for t in selected:
        y = (t.get("merged_at") or "?")[:4]
        year_dist[y] = year_dist.get(y, 0) + 1
    print(f"\nYear distribution: {dict(sorted(year_dist.items()))}")
    unit_count = sum(1 for t in selected if not t.get("is_integration_test"))
    print(f"Unit: {unit_count}, Integration: {len(selected) - unit_count}")

    # Write selected list
    out = TASKS_DIR / "selected_30.json"
    out.write_text(json.dumps([t["_file"] for t in selected], indent=2))
    print(f"\nSaved selection to {out}")


if __name__ == "__main__":
    main()
