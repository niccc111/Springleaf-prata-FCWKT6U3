#!/usr/bin/env bash
#
# Build and (re)start the production stack on the Lightsail host.
# Run from the repository root:
#
#   bash deploy/lightsail/deploy.sh
#
set -euo pipefail

# Resolve repo root regardless of where the script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

ENV_FILE="backend/.env.prod"
COMPOSE_FILE="docker-compose.prod.yml"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "ERROR: ${ENV_FILE} not found."
  echo "  cp backend/.env.prod.example ${ENV_FILE}   # then edit the CHANGE_ME values"
  exit 1
fi

if grep -q "CHANGE_ME" "${ENV_FILE}"; then
  echo "ERROR: ${ENV_FILE} still contains CHANGE_ME placeholders. Fill them in first."
  grep -n "CHANGE_ME" "${ENV_FILE}" || true
  exit 1
fi

echo "==> Building and starting the production stack"
docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" up -d --build

echo
echo "==> Running containers:"
docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" ps

echo
echo "==> The app is served on port 80. Confirm the API is healthy:"
echo "    curl -f http://localhost/health"
