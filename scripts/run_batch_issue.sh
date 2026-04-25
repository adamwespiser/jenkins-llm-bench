#!/usr/bin/env bash
# run_batch_issue.sh — Run issue_report and level1_full on the 21 tasks with issue reports
#
# Usage:
#   ./run_batch_issue.sh [--model MODEL] [--max-turns N] [--budget B]
#
# Results go to ~/bench/results-issue-desc/

set -euo pipefail

MODEL="${MODEL:-claude-sonnet-4-6}"
MAX_TURNS="${MAX_TURNS:-40}"
BUDGET="${BUDGET:-2.0}"
TASKS_FILE="$HOME/bench/tasks/selected_21.json"
SCRIPT="$HOME/bench/scripts/run_task.py"
VENV="$HOME/venvs/llm-bench/bin/activate"
RESULTS_DIR="$HOME/bench/results-issue-desc"

CONDITIONS=(issue_report level1_full)

while [[ $# -gt 0 ]]; do
  case $1 in
    --model)     MODEL="$2";     shift 2 ;;
    --max-turns) MAX_TURNS="$2"; shift 2 ;;
    --budget)    BUDGET="$2";    shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

source "$VENV"

TASK_FILES=$(python3 -c "import json; [print(f) for f in json.load(open('$TASKS_FILE'))]")
TOTAL=$(echo "$TASK_FILES" | wc -l)
TOTAL_RUNS=$((TOTAL * ${#CONDITIONS[@]}))

echo "Running $TOTAL tasks × ${#CONDITIONS[@]} conditions = $TOTAL_RUNS runs"
echo "Conditions: ${CONDITIONS[*]}"
echo "Model: $MODEL | Max turns: $MAX_TURNS | Budget: \$$BUDGET"
echo "Results dir: $RESULTS_DIR"
echo "Started: $(date -u)"
echo ""

PASS=0; FAIL=0; ERROR=0; RUN=0

for TASK_FILE in $TASK_FILES; do
  TASK_ID=$(python3 -c "import json; print(json.load(open('$TASK_FILE'))['id'])")

  for CONDITION in "${CONDITIONS[@]}"; do
    RUN=$((RUN + 1))
    echo "[$RUN/$TOTAL_RUNS] $TASK_ID :: $CONDITION"

    if python3 "$SCRIPT" "$TASK_FILE" \
        --condition "$CONDITION" \
        --model "$MODEL" \
        --max-turns "$MAX_TURNS" \
        --budget "$BUDGET" \
        --results-dir "$RESULTS_DIR"; then
      PASS=$((PASS + 1))
    else
      EXIT=$?
      if [[ $EXIT -eq 1 ]]; then
        FAIL=$((FAIL + 1))
      else
        ERROR=$((ERROR + 1))
      fi
    fi

    echo ""
  done
done

echo "=============================="
echo "Done: $(date -u)"
echo "Pass: $PASS | Fail: $FAIL | Error: $ERROR | Total: $TOTAL_RUNS"
