#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s [user@host] [remote_saleor_platform_dir] [ssh_key]\n' "$0"
  printf '\n'
  printf 'Defaults:\n'
  printf '  user@host:                  saleor@coreaxissolutions.in\n'
  printf '  remote_saleor_platform_dir: ~/saleor-platform\n'
  printf '  ssh_key:                    ../personal\n'
  printf '\n'
  printf 'Example:\n'
  printf '  %s saleor@coreaxissolutions.in ~/saleor-platform ../personal\n' "$0"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

REMOTE="${1:-saleor@coreaxissolutions.in}"
REMOTE_DIR="${2:-~/saleor-platform}"
SSH_KEY="${3:-${PROJECT_DIR}/../personal}"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

require_command ssh
require_command rsync

cd "${PROJECT_DIR}"

for path in custom-commands seed-assets; do
  if [[ ! -e "${path}" ]]; then
    printf 'Required path not found: %s/%s\n' "${PROJECT_DIR}" "${path}" >&2
    exit 1
  fi
done

if [[ ! -f "${SSH_KEY}" ]]; then
  printf 'SSH key not found: %s\n' "${SSH_KEY}" >&2
  exit 1
fi

printf 'Deploying seed command and assets to %s:%s\n' "${REMOTE}" "${REMOTE_DIR}"
printf 'Using SSH key: %s\n' "${SSH_KEY}"

ssh -i "${SSH_KEY}" "${REMOTE}" "mkdir -p ${REMOTE_DIR}/custom-commands ${REMOTE_DIR}/seed-assets"

rsync -az --delete \
  -e "ssh -i ${SSH_KEY}" \
  --exclude '.DS_Store' \
  custom-commands/ \
  "${REMOTE}:${REMOTE_DIR}/custom-commands/"

rsync -az --delete \
  -e "ssh -i ${SSH_KEY}" \
  --exclude '.DS_Store' \
  seed-assets/ \
  "${REMOTE}:${REMOTE_DIR}/seed-assets/"

printf 'Done.\n'
printf 'Remote compose mounts should now find:\n'
printf '  %s/custom-commands/populatedb_exlynatural.py\n' "${REMOTE_DIR}"
printf '  %s/seed-assets/exlynatural\n' "${REMOTE_DIR}"
