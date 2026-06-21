#!/usr/bin/env bash
# Developer helper for the Pexafy MCP server — everything that is NOT the MCP
# server itself: environment setup, local run, MCP Inspector, smoke test, Docker
# build. (Asset re-vendoring lives in prepare.sh.)
#
# Usage: ./run.sh <command>
set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv"
PY="$VENV/bin/python"

# Load .env if present (API base URL, optional keys) — never committed.
[ -f .env ] && set -a && . ./.env && set +a || true

_need_venv() { [ -x "$PY" ] || { echo "Run './run.sh setup' first."; exit 1; }; }

cmd_setup() {            # create venv + install (editable, with dev tools) + seed .env
  [ -d "$VENV" ] || python3 -m venv "$VENV"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -e ".[dev]"
  [ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example — fill it in."; }
  echo "Setup done."
}

cmd_dev() {             # run over stdio (Claude Desktop/Code, local)
  _need_venv
  PEXAFY_MCP_TRANSPORT=stdio "$PY" -m pexafy_mcp
}

cmd_run() {            # run the HTTP (Streamable) server locally
  _need_venv
  PEXAFY_MCP_TRANSPORT=http "$PY" -m pexafy_mcp
}

cmd_inspect() {        # open the MCP Inspector against the stdio command
  _need_venv
  npx @modelcontextprotocol/inspector "$PY" -m pexafy_mcp
}

cmd_test() {           # run the (offline) test suite
  _need_venv
  "$PY" -m pytest -q "$@"
}

cmd_build() {          # build the Docker image
  docker build -t pexafy-mcp .
}

case "${1:-help}" in
  setup) cmd_setup ;;
  dev) cmd_dev ;;
  run) cmd_run ;;
  inspect) cmd_inspect ;;
  test) shift; cmd_test "$@" ;;
  build) cmd_build ;;
  *) cat <<USAGE
Pexafy MCP — developer helper
  ./run.sh setup        create venv, install (editable), seed .env
  ./run.sh dev          run over stdio (Claude Desktop/Code)
  ./run.sh run          run the HTTP server locally
  ./run.sh inspect      open the MCP Inspector
  ./run.sh test         run the offline test suite (pytest)
  ./run.sh build        docker build
USAGE
    ;;
esac
