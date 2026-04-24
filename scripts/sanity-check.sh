#!/usr/bin/env bash
set -euo pipefail

echo "Java:"
java -version

echo
echo "Maven:"
mvn -version

echo
echo "Docker:"
docker version

echo
echo "GitHub CLI:"
gh --version

echo
echo "Python:"
source ~/venvs/llm-bench/bin/activate
python --version

echo
echo "Claude Code:"
claude --version || true

echo
echo "Jenkins repo:"
cd ~/bench/repos/jenkins
git rev-parse --show-toplevel
git status --short
