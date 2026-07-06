#!/usr/bin/env bash
# PreToolUse hook: block edits to lint/test/CI config files.
# Forces fixing the code instead of loosening the rules that catch it.
set -euo pipefail

input="$(cat)"
file="$(jq -r '.tool_input.file_path // .tool_input.path // empty' <<< "$input")"

[ -n "$file" ] || exit 0

case "$file" in
  */pyproject.toml|pyproject.toml|*/pytest.ini|pytest.ini|*/.ruff.toml|.ruff.toml|*/.github/workflows/*)
    jq -n --arg file "$file" '{
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        permissionDecision: "ask",
        permissionDecisionReason: ("設定/CIファイル(" + $file + ")の編集は要注意です。リンター/テストのルールを緩めるための変更ではなく、コード側を修正する意図であることを確認してください。")
      }
    }'
    ;;
esac
