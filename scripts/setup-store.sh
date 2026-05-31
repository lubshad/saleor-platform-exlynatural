#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_DIR}"

docker compose run --rm api python3 manage.py migrate
docker compose run --rm api python3 manage.py populatedb_exlynatural --createsuperuser "$@"
