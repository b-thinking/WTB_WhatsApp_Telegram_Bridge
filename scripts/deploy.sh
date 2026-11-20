#!/usr/bin/env bash

set -euo pipefail

# --------------------------------------------------
# Configuration
# --------------------------------------------------

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# --------------------------------------------------
# Validate parameters
# --------------------------------------------------

SOURCE_DIR="$REPO_DIR/container"
ENV_FILE="${REPO_DIR}/.env"

if [[ ! -d "$SOURCE_DIR" ]]; then
    echo "ERROR: Source directory not found: $SOURCE_DIR" >&2
    exit 1
fi

# --------------------------------------------------
# Load environment variables
# --------------------------------------------------
# NOTICE: .env file contains the default environment variables for create container file

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: Environment file not found: $ENV_FILE" >&2
    return 1
fi

echo "Loading environment file: $ENV_FILE"
set -a
source "${ENV_FILE}"
set +a

if [[ -f "${ENV_FILE}.prod" ]]; then
    set -a
    source "${ENV_FILE}.prod"
    set +a
fi


# --------------------------------------------------
# Validate deployment configuration
# --------------------------------------------------

if [[ -z "${DEPLOY_HOST:-}" ]]; then
    echo "ERROR: DEPLOY_HOST variable required in .env" >&2
    exit 1
fi

DEST_DIR="~/.config/containers/systemd"
DATA_DIR="${DEPLOY_VM_DIR}/${GHCR_TAG}/data"

# --------------------------------------------------
# Check dependencies
# --------------------------------------------------
for CMD in ssh envsubst rsync; do
    if ! command -v "$CMD" >/dev/null 2>&1; then
        echo "ERROR: $CMD is not installed." >&2
        exit 1
    fi
done


# --------------------------------------------------
# Create temporary staging directory
# --------------------------------------------------

umask 077
STAGE_DIR="$(mktemp -d)"

trap 'rm -rf "$STAGE_DIR"' EXIT

echo "Source:      $SOURCE_DIR"
echo "Destination: $DEST_DIR"
echo "Data dir:    ${DATA_DIR}"
echo

# --------------------------------------------------
# Generate configuration files
# --------------------------------------------------

while IFS= read -r -d '' SOURCE_FILE; do

    RELATIVE_PATH="${SOURCE_FILE#"$SOURCE_DIR"/}"
    STAGE_FILE="$STAGE_DIR/$RELATIVE_PATH"

    mkdir -p "$(dirname "$STAGE_FILE")"

    echo "Generating: ${RELATIVE_PATH}: ${SOURCE_FILE} -> ${STAGE_FILE}"

    envsubst < "$SOURCE_FILE" > "$STAGE_FILE"

done < <(
    find "$SOURCE_DIR" \
        -type f \
        ! -name 'Dockerfile' \
        -print0
)

# Set directory and file permissions
find "$STAGE_DIR" -type d -exec chmod 755 {} +
find "$STAGE_DIR" -type f -exec chmod 600 {} +

# --------------------------------------------------
# Deploy
# --------------------------------------------------

echo
echo "Transferring files..."

echo "Creating container dir: ${DEST_DIR}"
ssh "$DEPLOY_HOST" "mkdir -p ${DEST_DIR}"
echo "Creating data dir: ${DATA_DIR}"
ssh "$DEPLOY_HOST" "mkdir -p ${DATA_DIR}"

rsync -av \
    "${STAGE_DIR}/" \
    "${DEPLOY_HOST}:${DEST_DIR}/"
echo
echo "Deployment completed successfully."
