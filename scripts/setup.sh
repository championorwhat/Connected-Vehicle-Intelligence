#!/usr/bin/env bash
# Checks (and optionally installs, with --install) the local toolchain on macOS.
#   ./scripts/setup.sh --check     # report only (default)
#   ./scripts/setup.sh --install   # brew-install missing tools
set -uo pipefail

MODE="${1:---check}"
ok=0; missing=()

green() { printf "\033[32m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*"; }
yellow(){ printf "\033[33m%s\033[0m\n" "$*"; }

check() { # name, command, min-hint
  if command -v "$2" >/dev/null 2>&1; then
    green "  ✔ $1: $($2 --version 2>&1 | head -1)"
  else
    red "  ✘ $1 not found ($3)"; missing+=("$1"); ok=1
  fi
}

echo "Toolchain"
check git     git     "xcode-select --install"
check make    make    "xcode-select --install"
check docker  docker  "install Docker Desktop"
check uv      uv      "brew install uv"
check node    node    "brew install node@22"
check npm     npm     "comes with node"

if command -v docker >/dev/null 2>&1; then
  echo "Docker engine"
  if docker info >/dev/null 2>&1; then
    mem_bytes=$(docker info --format '{{.MemTotal}}')
    cpus=$(docker info --format '{{.NCPU}}')
    mem_gb=$(awk "BEGIN {printf \"%.1f\", $mem_bytes/1024/1024/1024}")
    green "  ✔ running: ${cpus} CPUs, ${mem_gb} GB memory"
    if awk "BEGIN {exit !($mem_bytes < 4.5*1024*1024*1024)}"; then
      yellow "  ! Docker has < 4.5 GB. Docker Desktop → Settings → Resources → Memory: 5 GB"
    fi
    docker compose version >/dev/null 2>&1 && green "  ✔ docker compose: $(docker compose version --short)" \
      || { red "  ✘ docker compose plugin missing"; ok=1; }
  else
    red "  ✘ Docker is installed but not running — open Docker Desktop"; ok=1
  fi
fi

if [[ "$MODE" == "--install" && ${#missing[@]} -gt 0 ]]; then
  command -v brew >/dev/null || { red "Homebrew missing: see https://brew.sh"; exit 1; }
  for t in "${missing[@]}"; do
    case "$t" in
      uv)   brew install uv ;;
      node|npm) brew install node@22 && brew link --overwrite node@22 ;;
      docker) yellow "Install Docker Desktop manually: https://www.docker.com/products/docker-desktop/" ;;
      git|make) xcode-select --install || true ;;
    esac
  done
fi

[[ $ok -eq 0 ]] && green "All good." || red "Fix the items above, then re-run: make doctor"
exit $ok
