#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_PATH="${1:-/tmp/youtube_automation_deploy.tar.gz}"

mkdir -p "$(dirname "$OUTPUT_PATH")"
rm -f "$OUTPUT_PATH"

export COPYFILE_DISABLE=1

tar \
  -C "$ROOT_DIR" \
  --exclude='.git' \
  --exclude='.github' \
  --exclude='.idea' \
  --exclude='.vscode' \
  --exclude='.venv' \
  --exclude='.pytest_cache' \
  --exclude='.ruff_cache' \
  --exclude='.coverage' \
  --exclude='.coverage.*' \
  --exclude='.DS_Store' \
  --exclude='._*' \
  --exclude='artifacts' \
  --exclude='backend/.venv' \
  --exclude='backend/.pytest_cache' \
  --exclude='backend/artifacts' \
  --exclude='frontend/node_modules' \
  --exclude='frontend/dist' \
  --exclude='frontend/.next' \
  --exclude='**/__pycache__' \
  --exclude='**/*.pyc' \
  -czf "$OUTPUT_PATH" \
  .

echo "$OUTPUT_PATH"
