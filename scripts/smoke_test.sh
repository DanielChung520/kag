#!/usr/bin/env bash
# End-to-end smoke test for kag.
#
# Verifies the happy path against a running kag instance:
#   1. /health is reachable
#   2. Admin token can create a KB
#   3. Per-KB API key can upload a text file
#   4. Per-KB API key can list files
#   5. Per-KB API key can run /hybrid/search
#
# Exits non-zero on the first failure. Designed to run in CI or
# against a freshly-deployed kag (port 8800, admin token + env
# vars already set in the calling shell).
#
# Usage:
#   export KAG_BASE_URL=http://localhost:8800
#   export KAG_ADMIN_TOKEN=...
#   ./scripts/smoke_test.sh

set -euo pipefail

BASE="${KAG_BASE_URL:-http://localhost:8800}"
ADMIN="${KAG_ADMIN_TOKEN:?KAG_ADMIN_TOKEN must be set}"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[34m%s\033[0m\n' "$*"; }

assert_status() {
    local expected="$1"; local got="$2"; local label="$3"
    if [[ "$got" != "$expected" ]]; then
        red "FAIL [$label] expected HTTP $expected, got $got"
        exit 1
    fi
    green "OK   [$label] HTTP $got"
}

blue "[1/5] /health"
status=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/health")
assert_status 200 "$status" "health"

blue "[2/5] POST /knowledge-bases"
TMP_KB=$(mktemp)
trap 'rm -f "$TMP_KB"' EXIT
status=$(curl -s -o "$TMP_KB" -w "%{http_code}" -X POST "$BASE/api/v1/knowledge-bases" \
    -H "Authorization: Bearer $ADMIN" \
    -H "Content-Type: application/json" \
    -d '{"name":"smoke-test","ontology_major_key":"manufacturing_v1"}')
assert_status 201 "$status" "kb-create"
KB_KEY=$(python3 -c "import json; print(json.load(open('$TMP_KB'))['kb_key'])")
API_KEY=$(python3 -c "import json; print(json.load(open('$TMP_KB'))['api_key'])")
blue "      kb_key=$KB_KEY"

blue "[3/5] POST /files (multipart)"
TMP_FILE=$(mktemp)
echo "kag smoke test $(date -u +%FT%TZ)" > "$TMP_FILE"
status=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "$BASE/api/v1/knowledge-bases/$KB_KEY/files" \
    -H "X-KAG-API-Key: $API_KEY" \
    -F "file=@$TMP_FILE")
assert_status 201 "$status" "file-upload"

blue "[4/5] GET /files (list)"
TMP_LIST=$(mktemp)
status=$(curl -s -o "$TMP_LIST" -w "%{http_code}" \
    "$BASE/api/v1/knowledge-bases/$KB_KEY/files" \
    -H "X-KAG-API-Key: $API_KEY")
assert_status 200 "$status" "file-list"
count=$(python3 -c "import json; print(len(json.load(open('$TMP_LIST')).get('files', [])))")
if [[ "$count" -lt 1 ]]; then
    red "FAIL [file-list] expected at least 1 file, got $count"
    exit 1
fi
green "OK   [file-list] $count file(s)"

blue "[5/5] POST /hybrid/search"
TMP_SEARCH=$(mktemp)
status=$(curl -s -o "$TMP_SEARCH" -w "%{http_code}" -X POST \
    "$BASE/api/v1/hybrid/search" \
    -H "X-KAG-API-Key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"query":"kag smoke test","top_k":5,"top_n":3}')
assert_status 200 "$status" "hybrid-search"
query_type=$(python3 -c "import json; print(json.load(open('$TMP_SEARCH'))['query_type'])")
green "OK   [hybrid-search] query_type=$query_type"

green ""
green "All 5 smoke checks passed against $BASE"
