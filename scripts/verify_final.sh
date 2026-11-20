#!/usr/bin/env bash
set -euo pipefail
BASE_URL="${NEXUSOPS_BASE_URL:-http://127.0.0.1:18081}"
USERNAME="${NEXUSOPS_USER:-engineer}"
PASSWORD="${NEXUSOPS_PASSWORD:-}"
if [[ -z "$PASSWORD" ]]; then
  read -r -s -p "Password for ${USERNAME}: " PASSWORD
  echo
fi
AUTH="$(printf '%s' "$USERNAME:$PASSWORD" | base64 | tr -d '\n')"

echo "NexusOps final verification: $BASE_URL"
curl -fsS "$BASE_URL/actuator/health" | python -c 'import json,sys; d=json.load(sys.stdin); assert d.get("status")=="UP",d; print("[PASS] health UP")'
curl -fsS -H "Authorization: Basic $AUTH" "$BASE_URL/api/profile" | python -c 'import json,sys; d=json.load(sys.stdin); print("[PASS] profile:",d.get("username"))'
curl -fsS -H "Authorization: Basic $AUTH" "$BASE_URL/api/dashboard/summary" | python -c 'import json,sys; d=json.load(sys.stdin); assert "metrics" in d; print("[PASS] dashboard metrics")'
curl -fsS -H "Authorization: Basic $AUTH" "$BASE_URL/api/incidents?page=1&pageSize=10" | python -c 'import json,sys; d=json.load(sys.stdin); assert "items" in d; print("[PASS] incident list")'
curl -fsS -H "Authorization: Basic $AUTH" "$BASE_URL/api/alerts?limit=10" | python -c 'import json,sys; d=json.load(sys.stdin); assert isinstance(d,list); print("[PASS] alert list")'
echo "All NexusOps final checks passed."
