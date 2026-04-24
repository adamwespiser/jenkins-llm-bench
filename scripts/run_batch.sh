#!/usr/bin/env bash
# run_batch.sh — Run all three conditions for each selected task
#
# Usage:
#   ./run_batch.sh [--model MODEL] [--max-turns N] [--budget B]
#
# Reads task list from ~/bench/tasks/selected_30.json
# Results go to ~/bench/results/
# Logs go to ~/bench/logs/

set -euo pipefail

MODEL="${MODEL:-claude-sonnet-4-6}"
MAX_TURNS="${MAX_TURNS:-20}"
BUDGET="${BUDGET:-2.0}"
TASKS_FILE="$HOME/bench/tasks/selected_30.json"
SCRIPT="$HOME/bench/scripts/run_task.py"
VENV="$HOME/venvs/llm-bench/bin/activate"

CONDITIONS=(level1_full level2_full level2_module)

if [[ ! -f "$TASKS_FILE" ]]; then
  echo "ERROR: $TASKS_FILE not found. Run select_tasks.py first."
  exit 1
fi

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --model)    MODEL="$2";     shift 2 ;;
    --max-turns) MAX_TURNS="$2"; shift 2 ;;
    --budget)   BUDGET="$2";    shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

source "$VENV"

TASK_FILES=$(python3 -c "import json; [print(f) for f in json.load(open('$TASKS_FILE'))]")
TOTAL=$(echo "$TASK_FILES" | wc -l)
echo "Running $TOTAL tasks × ${#CONDITIONS[@]} conditions = $((TOTAL * ${#CONDITIONS[@]})) runs"
echo "Model: $MODEL | Max turns: $MAX_TURNS | Budget: \$$BUDGET"
echo "Started: $(date -u)"
echo ""

PASS=0; FAIL=0; ERROR=0; RUN=0
TOTAL_RUNS=$((TOTAL * ${#CONDITIONS[@]}))

for TASK_FILE in $TASK_FILES; do
  TASK_ID=$(python3 -c "import json; print(json.load(open('$TASK_FILE'))['id'])")

  for CONDITION in "${CONDITIONS[@]}"; do
    RUN=$((RUN + 1))
    echo "[$RUN/$TOTAL_RUNS] $TASK_ID :: $CONDITION"

    if python3 "$SCRIPT" "$TASK_FILE" \
        --condition "$CONDITION" \
        --model "$MODEL" \
        --max-turns "$MAX_TURNS" \
        --budget "$BUDGET"; then
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
