#!/usr/bin/env bash
# Runs once when the Codespace is created: tools, .env with a fresh demo password, Python deps.
set -euo pipefail

curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

make env
# A per-Codespace demo password instead of the shared placeholder (the dashboard port may be public).
if grep -q '^DEMO_USER_PASSWORD=change-me-local-only$' .env; then
  sed -i "s/^DEMO_USER_PASSWORD=.*/DEMO_USER_PASSWORD=$(openssl rand -hex 12)/" .env
fi

uv sync --group dev

cat <<'MSG'

Prognos is ready. Start the demo (about 5 minutes for the first image builds):

  make codespace-demo

Then open the "Dashboard" tab under PORTS (8080). Sign in as fleet_manager@demo.prognos.local
with the DEMO_USER_PASSWORD from .env (grep DEMO_USER_PASSWORD .env).
MSG
