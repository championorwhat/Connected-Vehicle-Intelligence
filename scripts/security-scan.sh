#!/usr/bin/env bash
# Local equivalent of .github/workflows/security.yml (filesystem part): the same gate
# (fails on any HIGH/CRITICAL finding not in .trivyignore). Report: evidence/security/.
# Behind a TLS-intercepting proxy, pass its CA: TRIVY_CA=/path/to/ca.crt ./scripts/security-scan.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p evidence/security
ca_args=()
if [[ -n "${TRIVY_CA:-}" ]]; then
  ca_args=(--network host -e HTTPS_PROXY -e HTTP_PROXY -e NO_PROXY -e SSL_CERT_FILE=/ca.crt
           -v "$TRIVY_CA:/ca.crt:ro")
fi
docker run --rm "${ca_args[@]}" -v "$PWD":/src:ro aquasec/trivy:0.67.2 fs \
  --scanners vuln,misconfig,secret --severity CRITICAL,HIGH \
  --skip-dirs /src/.venv --skip-dirs /src/apps/web/node_modules \
  --ignorefile /src/.trivyignore --exit-code 1 --format table /src \
  | tee evidence/security/trivy-fs.txt
