#!/usr/bin/env bash
# PostToolUse hook: auto-fix and lint Python files after Write/Edit/MultiEdit.
set -euo pipefail

cd "$(dirname "$0")/../.."

input="$(cat)"
file="$(jq -r '.tool_input.file_path // .tool_input.path // empty' <<< "$input")"

case "$file" in
  *.py) ;;
  *) exit 0 ;;
esac

[ -f "$file" ] || exit 0

uv run ruff format "$file" >/dev/null 2>&1 || true
uv run ruff check --fix "$file" >/dev/null 2>&1 || true

diag="$(uv run ruff check --output-format=concise "$file" 2>&1 | head -40 || true)"

if [ -n "$diag" ] && [ "$diag" != "All checks passed!" ]; then
  jq -Rn --arg msg "$diag" '{
    hookSpecificOutput: {
      hookEventName: "PostToolUse",
      additionalContext: $msg
    }
  }'
fi
