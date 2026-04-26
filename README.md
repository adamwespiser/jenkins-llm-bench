# Jenkins LLM Benchmark

SWE-bench-style evaluation harness testing LLM coding agents on bug fixes in the
[jenkinsci/jenkins](https://github.com/jenkinsci/jenkins) repository (~500k lines of Java).

**Thesis:** LLM agents fail on large legacy codebases not because of intelligence limits but because the
correct fix location and approach are non-obvious from the bug description alone — a "coffin corner"
of context retrieval and domain knowledge.

## Repository layout

```
bench/
  tasks/               # Task JSON files (one per PR)
  results-issue-desc/  # Sonnet 4.6 runs (issue_report + level1_full conditions)
  results-issue-opus-4-7/  # Opus 4.7 runs (issue_report condition)
  scripts/
    mine_tasks.py      # Mine merged fix PRs from jenkinsci/jenkins
    select_tasks.py    # Filter and select task set
    validate_tasks.py  # Validate base-fail / fixed-pass for each task
    run_task.py        # Single-task harness (worktree → inject → agent → validate)
    run_parallel.py    # Parallel batch runner
    run_batch_opus.sh  # Sequential batch runner (Opus 4.7 defaults)
  experiment_results.csv   # Aggregated Sonnet 4.6 results
```

## Reproducing the Opus 4.7 experiment

### Prerequisites

- GCP VM or equivalent: 16 vCPU, 64 GB RAM recommended
- Java 17+, Maven 3.9+
- `ANTHROPIC_API_KEY` set in environment
- Jenkins repo cloned at `~/bench/repos/jenkins`
- Python venv at `~/venvs/llm-bench` with `anthropic`, `claude-agent-sdk`, `litellm`

```bash
python3 -m venv ~/venvs/llm-bench
source ~/venvs/llm-bench/bin/activate
pip install anthropic claude-agent-sdk litellm
```

### Warm the Maven cache

Run any test once to pull dependencies (~80s first run):

```bash
cd ~/bench/repos/jenkins
mvn test -pl core -am -Dtest=CompositeCauseOfBlockageTest -DfailIfNoTests=false \
  --no-transfer-progress -Denforcer.skip=true -q
```

### Run the experiment

The task set (`tasks/selected_21.json`) and all task files are included. To reproduce
the Opus 4.7 `issue_report` run:

```bash
source ~/venvs/llm-bench/bin/activate

python3 scripts/run_parallel.py \
  --model claude-opus-4-7 \
  --conditions issue_report \
  --tasks-file tasks/selected_21.json \
  --results-dir results-issue-opus-4-7 \
  --max-turns 80 \
  --budget 8.0 \
  --workers 8
```

Or in a detached tmux session:

```bash
tmux new-session -d -s opus-bench \
  'cd ~/bench && source ~/venvs/llm-bench/bin/activate && \
   python3 scripts/run_parallel.py 2>&1 | tee logs/opus_$(date +%Y%m%d_%H%M%S).log'
```

Expected cost: ~$44 for 21 tasks. Expected runtime: ~35 minutes wall-clock at 8 workers.

### Reproduce the Sonnet 4.6 baseline

```bash
python3 scripts/run_parallel.py \
  --model claude-sonnet-4-6 \
  --conditions issue_report \
  --tasks-file tasks/selected_21.json \
  --results-dir results-issue-sonnet-repro \
  --max-turns 40 \
  --budget 2.0 \
  --workers 8
```

### Important Maven flag

Always include `-Denforcer.skip=true`. The `requireExtensionVersion` enforcer rule
fails on historical commits with Maven 3.9+.

## Results summary (2026-04-26)

| Model | Pass rate | Total cost |
|---|---|---|
| Sonnet 4.6 | 12/21 (57.1%) | ~$9 |
| Opus 4.7 | 13/21 (61.9%) | $44.32 |

Full per-task results with Sonnet vs Opus comparison:
`results-issue-opus-4-7/experiment_summary_opus_4_7.json`

**Notable:** Opus solved `jenkins-10326` (Sonnet failed with 361 turns, zero edits).
The 8 tasks that failed both models share a pattern: the fix is architecturally
non-obvious — requiring knowledge of Jenkins internals that is not surfaced by the
bug description or test failure alone.

**Confound:** Opus ran with `--max-turns 80` vs Sonnet's `--max-turns 40`. The
jenkins-10326 flip may be partly attributable to the higher turn budget rather than
model capability alone.
