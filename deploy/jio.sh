#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")/.."
vm="${JIO_VM_ID:?Set JIO_VM_ID to the dedicated Blaubeere VM}"
revision="${GIT_REF:-$(git rev-parse HEAD)}"
[[ "$vm" =~ ^[0-9a-f]{32}$ && "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected a VM ID and full commit SHA' >&2; exit 1; }
[[ "$(jio usage)" == "Account: blaubeere ("* ]] || { echo 'Production deployments require the blaubeere Jio account' >&2; exit 1; }

if readiness="$(jio exec "$vm" true --timeout 15 2>&1)"; then
  :
elif [[ "$readiness" == "jio: session $vm is Stopped" ]]; then
  jio start "$vm"
  jio exec "$vm" true --timeout 15
else
  printf '%s\n' "$readiness" >&2
  exit 1
fi

published="$(jio ports "$vm")"
endpoint() {
  local url
  url="$(awk -v port="$1" '$1 == port && $2 == "published" { print $3; exit }' <<< "$published")"
  [[ -n "$url" ]] || url="$(jio expose "$1" "$vm")"
  [[ "$url" =~ ^https://[a-zA-Z0-9.-]+(:[0-9]+)?$ ]] || { echo 'Jio returned an invalid HTTPS origin' >&2; return 1; }
  printf '%s' "$url"
}
app="$(endpoint 8080)"
landing="$(endpoint 3102)"

# A complete heredoc prevents commands such as installers from consuming the SSH input.
{
  printf "bash -s -- '%s' '%s' '%s' <<'BLAUBEERE_DEPLOY_SCRIPT'\n" "$revision" "$app" "$landing"
  cat deploy/runtime.sh
  printf '\nBLAUBEERE_DEPLOY_SCRIPT\n'
} | jio connect "$vm"

# Match runtime.sh: exercise authenticated demo/MCP separately from the public frontend demo.
DEMO_LOGIN=true bun scripts/check-production.ts "$app" "$landing"
printf 'App: %s\nLanding: %s\nMCP: %s/mcp\nRevision: %s\n' "$app" "$landing" "$app" "$revision"
if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  printf '### Blaubeere deployed\n\n- [App](%s)\n- [Landing](%s)\n- MCP: %s/mcp\n- Commit: %s\n' "$app" "$landing" "$app" "$revision" >> "$GITHUB_STEP_SUMMARY"
fi
