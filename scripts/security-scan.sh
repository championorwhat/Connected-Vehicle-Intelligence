#!/usr/bin/env bash
# Local equivalent of .github/workflows/security.yml (filesystem part).
# Writes the report to evidence/security/.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p evidence/security
docker run --rm -v "$PWD":/src:ro aquasec/trivy:0.67.2 fs \
  --scanners vuln,misconfig,secret --severity CRITICAL,HIGH \
  --skip-dirs /src/.venv --exit-code 0 --format table /src \
  | tee evidence/security/trivy-fs.txt
