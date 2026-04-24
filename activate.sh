#!/usr/bin/env bash
source ~/venvs/llm-bench/bin/activate
export BENCH_HOME="$HOME/bench"
export JENKINS_REPO="$HOME/bench/repos/jenkins"
cd "$BENCH_HOME"
echo "Activated benchmark env"
echo "BENCH_HOME=$BENCH_HOME"
echo "JENKINS_REPO=$JENKINS_REPO"
