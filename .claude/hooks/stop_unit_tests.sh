#!/usr/bin/env bash
# Stop hook: block completion until unit tests pass.
set -euo pipefail

cd "$(dirname "$0")/../.."

if ! output="$(uv run pytest tests/unit/ -q --no-header 2>&1)"; then
  summary="$(echo "$output" | grep -E '^(FAILED|ERROR)|short test summary|passed|failed' | tail -30)"
  jq -Rn --arg msg "$summary" '{
    decision: "block",
    reason: ("uv run pytest tests/unit/ が失敗しています。修正してから完了を報告してください。\n\n" + $msg)
  }'
  exit 0
fi

exit 0
