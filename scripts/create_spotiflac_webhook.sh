#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   WEBHOOK_URL="https://your-domain/webhooks/spotiflac-proxies" \
#   REPO="owner/repo" \
#   WEBHOOK_SECRET="optional-secret" \
#   ./scripts/create_spotiflac_webhook.sh

REPO="${REPO:-BartolomeoRusso9/SpotiFLAC-Module-Version}"
WEBHOOK_URL="${WEBHOOK_URL:-}"
WEBHOOK_SECRET="${WEBHOOK_SECRET:-}"
if [[ -z "$WEBHOOK_URL" ]]; then
  echo "ERROR: WEBHOOK_URL is required"
  echo "Example: WEBHOOK_URL='https://your-domain/webhooks/spotiflac-proxies' REPO='owner/repo' ./scripts/create_spotiflac_webhook.sh"
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: GitHub CLI (gh) is not installed"
  exit 1
fi

if [[ -n "$WEBHOOK_SECRET" ]]; then
  SECRET_ARG=(-f "config[secret]=$WEBHOOK_SECRET")
else
  SECRET_ARG=()
fi

# Create webhook
create_output=$(gh api -X POST \
  "/repos/${REPO}/hooks" \
  -f "name=web" \
  -f "active=true" \
  -f "config[url]=$WEBHOOK_URL" \
  -f "config[content_type]=json" \
  -f "config[insecure_ssl]=0" \
  "${SECRET_ARG[@]}" \
  -f "events[]=push" \
  -f "events[]=release" \
  -f "events[]=workflow_run" \
  2>&1) || {
    echo "Failed to create webhook:"
    echo "$create_output"
    exit 1
  }

hook_id=$(echo "$create_output" | jq -r '.id // empty')
if [[ -n "$hook_id" ]]; then
  echo "Webhook created: id=$hook_id repo=$REPO url=$WEBHOOK_URL"
else
  echo "Webhook creation response:"
  echo "$create_output"
fi
