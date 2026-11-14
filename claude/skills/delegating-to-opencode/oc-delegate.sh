#!/usr/bin/env bash
# Run one opencode task on an Ollama cloud model inside an enforced permission set.
# Usage: oc-delegate.sh [-m model] [-r] [-a 'shell pattern'] [-s ses_slug] [-g agent] [-t title] BRIEF_FILE LOG_FILE
#   -m  opencode model id (default ollama/deepseek-v4.1-flash:1017-cloud)
#   -r  read-only: no file edits (research, review, audits)
#   -a  extra allowed shell pattern, repeatable (e.g. -a 'npm test*')
#   -s  session id to create or continue; must start with ses_ (reuse it for fix rounds)
#   -g  opencode agent, e.g. code-reviewer / code-explorer / code-architect (read-only, power pack)
# Run from the repo root: edits are allowed inside the current directory only.
# --standalone is required: without it `opencode run` uses the shared background service,
# which never sees OPENCODE_CONFIG_CONTENT, and every action is allowed.
# Never add --auto: it approves everything and Claude Code's auto mode blocks it.
set -uo pipefail
model="ollama/deepseek-v4.1-flash:1017-cloud"; readonly_mode=0; title="claude-delegate"; extra=(); session=(); agent=()
while getopts "m:ra:s:g:t:" opt; do
  case $opt in
    m) model=$OPTARG ;; r) readonly_mode=1 ;; a) extra+=("$OPTARG") ;; s) [[ $OPTARG == ses_* ]] || { echo "session id must start with ses_" >&2; exit 2; }; session=(-s "$OPTARG") ;; g) agent=(--agent "$OPTARG") ;; t) title=$OPTARG ;;
    *) sed -n 2,12p "$0"; exit 2 ;;
  esac
done
shift $((OPTIND - 1))
[[ $# -eq 2 ]] || { sed -n 2,12p "$0"; exit 2; }
brief=$1; log=$2
allow=("uv run pytest *" "uv run ruff *" "uv run mypy *"
       "git diff*" "git status*" "git log*" "git show*" "ls"
       "wc *")
allow+=("${extra[@]}")
shell_rules='"*":"deny"'
for p in "${allow[@]}"; do shell_rules+=",\"${p//\"/\\\"}\":\"allow\""; done
if [[ $readonly_mode -eq 1 ]]; then
  edit_rules='"deny"'
else
  # Edit paths are matched relative to the project: absolute or ../ paths are outside it.
  edit_rules='{"*":"allow","/*":"deny","../*":"deny"}'
fi
# Reads are confined to the project too: whatever a tool reads is sent to the cloud model.
# grep and glob are checked against their search pattern, not a path, so only
# external_directory (checked against the outside path itself) stops them leaving the repo.
inside='{"*":"allow","/*":"deny","~*":"deny","../*":"deny","*.env":"deny","*.env.*":"deny"}'
# "*":"deny" comes first: tools the rules below do not name stay denied. That covers every MCP
# server in the global config (playwright, replicate, ...), which the JavaScript `execute` tool can
# otherwise reach, bypassing the read and network rules. Later rules override earlier ones.
export OPENCODE_CONFIG_CONTENT="{\"permission\":{\"*\":\"deny\",\"todowrite\":\"allow\",\"todoread\":\"allow\",\"skill\":\"allow\",\"shell\":{$shell_rules},\"edit\":$edit_rules,\"read\":$inside,\"grep\":$inside,\"glob\":$inside,\"list\":$inside,\"external_directory\":\"deny\",\"webfetch\":\"deny\",\"websearch\":\"deny\",\"subagent\":\"deny\"}}"
timeout "${OC_TIMEOUT:-1500}" opencode run --standalone --archived --title "$title" "${session[@]}" "${agent[@]}" -m "$model" \
  "$(cat "$brief")" >"$log" 2>&1
status=$?
denied=$(grep -ac "Permission denied" "$log" || true)
# opencode can exit 0 on failure, so also surface error lines from the log.
errors=$(grep -acE "^Error:|EACCES|ProviderError" "$log" || true)
echo "opencode exit $status ($model), permission denials: $denied, error lines: $errors -> $log"
tail -40 "$log"
exit $status
