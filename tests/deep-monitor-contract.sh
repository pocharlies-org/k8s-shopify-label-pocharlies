#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
test_root=$(mktemp -d)
trap 'rm -rf -- "$test_root"' EXIT
fake_bin="$test_root/bin"
mkdir -p "$fake_bin"

awk '/^  monitor\.sh: \|$/{in_script=1; next} in_script && /^---$/{exit} in_script{sub(/^    /,""); print}' \
  "$repo_root/k8s/deep-monitor-cron.yaml" > "$test_root/monitor.sh"
bash -n "$test_root/monitor.sh"

cat > "$fake_bin/kubectl" <<'EOF'
#!/usr/bin/env bash
if [ "${DEEP_TEST_KUBE_FAIL:-0}" = 1 ]; then
  echo 'Error from server (Forbidden): denied by contract test' >&2
  exit 1
fi
case " $* " in
  *' get deploy '*)
    printf '%s\n' '{"spec":{"replicas":1,"template":{"spec":{"containers":[{"image":"labels:test"}]}}},"status":{"readyReplicas":1}}'
    ;;
  *' get pods '*)
    printf '%s\n' '{"items":[{"status":{"containerStatuses":[{"restartCount":0}]}}]}'
    ;;
  *' logs '*)
    :
    ;;
  *)
    echo "unexpected kubectl invocation: $*" >&2
    exit 2
    ;;
esac
EOF

cat > "$fake_bin/curl" <<'EOF'
#!/usr/bin/env bash
url=''
headers=0
options=0
for arg in "$@"; do
  case "$arg" in
    http://*|https://*) url="$arg" ;;
    -D) headers=1 ;;
    OPTIONS) options=1 ;;
  esac
done
if [[ "$url" == *'/api/v1/import/prometheus?'* ]]; then
  payload=$(cat)
  printf '%s\n' "$payload" >> "$DEEP_TEST_METRICS"
  [ "${DEEP_TEST_VM_FAIL:-0}" = 1 ] && exit 22
  exit 0
fi
if [ "$headers" -eq 1 ]; then
  printf 'HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: https://extensions.shopifycdn.com\r\n\r\n'
elif [[ "$url" == *'/admin/order-label'* ]]; then
  printf '%s' "${DEEP_TEST_ORDER_CODE:-200}"
elif [ "$options" -eq 1 ]; then
  printf '204'
else
  printf '401'
fi
EOF

cat > "$fake_bin/sleep" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$fake_bin/kubectl" "$fake_bin/curl" "$fake_bin/sleep"

run_case() {
  local name="$1" expected_exit="$2" metrics_file="$test_root/$1.metrics"
  shift 2
  : > "$metrics_file"
  set +e
  env PATH="$fake_bin:$PATH" DEEP_TEST_METRICS="$metrics_file" "$@" \
    bash "$test_root/monitor.sh" >"$test_root/$name.out" 2>"$test_root/$name.err"
  actual_exit=$?
  set -e
  if [ "$actual_exit" -ne "$expected_exit" ]; then
    echo "$name: expected exit $expected_exit, got $actual_exit" >&2
    sed -n '1,200p' "$test_root/$name.out" >&2
    sed -n '1,200p' "$test_root/$name.err" >&2
    exit 1
  fi
}

run_case healthy 0
grep -q 'labels_deep_monitor_check_ok.* 1 ' "$test_root/healthy.metrics"
grep -q 'labels_deep_monitor_incident_count.* 0 ' "$test_root/healthy.metrics"

run_case incident 0 DEEP_TEST_ORDER_CODE=500
grep -q 'labels_deep_monitor_check_ok.* 0 ' "$test_root/incident.metrics"
grep -q 'labels_deep_monitor_incident_count.* 1 ' "$test_root/incident.metrics"
grep -q '=== INCIDENTE PUBLICADO ===' "$test_root/incident.out"

run_case kubernetes_error 2 DEEP_TEST_KUBE_FAIL=1
test ! -s "$test_root/kubernetes_error.metrics"
grep -q '=== EXECUTION ERROR ===' "$test_root/kubernetes_error.out"

run_case metrics_error 2 DEEP_TEST_VM_FAIL=1
test "$(grep -c 'labels_deep_monitor_check_ok' "$test_root/metrics_error.metrics")" -eq 3
grep -q 'no se pudo persistir el resultado' "$test_root/metrics_error.err"

echo 'deep-monitor contract tests: PASS'
