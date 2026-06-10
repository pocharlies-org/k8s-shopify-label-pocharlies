#!/usr/bin/env bash
# One-shot digest pin for the whatsapp-tracking-activation release.
# Usage: scripts/pin-wa-activation-digests.sh <release-tag>
# Resolves each rebuilt image's digest from harbor and rewrites the pins.
set -euo pipefail
TAG="${1:?release tag}"
REG=harbor.e-dani.com/homelab
cd "$(dirname "$0")/.."

declare -A FILES=(
  [skirmshop-labels-shopify-app]="k8s/manifest.yaml k8s/sendcloud-scan-report-cron.yaml"
  [skirmshop-labels-notification-dispatcher]="k8s/synapse-services.yaml"
  [skirmshop-labels-tracking-ingestion]="k8s/synapse-services.yaml"
  [skirmshop-labels-whatsapp-optin-ingest]="k8s/whatsapp-optin-ingest.yaml"
)

for img in "${!FILES[@]}"; do
  digest=$(docker manifest inspect "$REG/$img:$TAG" -v 2>/dev/null \
    | python3 -c "import sys,json
d=json.load(sys.stdin)
d=d[0] if isinstance(d,list) else d
print(d['Descriptor']['digest'])")
  [ -n "$digest" ] || { echo "no digest for $img:$TAG" >&2; exit 1; }
  for f in ${FILES[$img]}; do
    sed -i -E "s#${REG}/${img}(@sha256:[0-9a-f]+|:latest)#${REG}/${img}@${digest}#g" "$f"
  done
  echo "$img -> $digest"
done
git diff --stat
