#! /usr/bin/env bash

# set -x
set -e
current_dir=$(pwd)

SOURCE_FILE="${BASH_SOURCE[0]}"
SCRIPT_DIR="$(cd -- "$(dirname -- "$SOURCE_FILE")" && pwd)"
WORKSPACE_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${WORKSPACE_DIR}/.env"

cd "${WORKSPACE_DIR}"

# --------------------------------------------------
# Load environment variables
# --------------------------------------------------
# NOTICE: .env file contains the GitHub Container Registry credentials

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: Environment file not found: $ENV_FILE" >&2
    return 1
fi

source "$ENV_FILE"

# GHCR_REGISTRY="ghcr.io/b-thinking"
# GHCR_TAG="bthinking-video-bot"
# GHCR_VERSION="0.0"
# GHCR_TOKEN=

# Start podman machine
command -v podman >/dev/null && podman machine start || true


# Login in GHCR
echo "${GHCR_TOKEN}"|podman login ghcr.io --username "${GHCR_USER}" --password-stdin 


# Arm image
echo "Building for arm64 architecture"


podman build \
    --progress=plain \
    --platform linux/arm64 \
    --no-cache \
    -f container/Dockerfile \
    -t ${GHCR_TAG}:arm64-${GHCR_VERSION} \
    .

podman tag ${GHCR_TAG}:arm64-${GHCR_VERSION} ${GHCR_REGISTRY}/${GHCR_TAG}:arm64-${GHCR_VERSION}
podman push ${GHCR_REGISTRY}/${GHCR_TAG}:arm64-${GHCR_VERSION}

echo "Building for amd64 architecture"

podman build \
    --progress=plain \
    --platform linux/amd64 \
    --no-cache \
    -f container/Dockerfile \
    -t ${GHCR_TAG}:amd64-${GHCR_VERSION} \
    .


podman tag ${GHCR_TAG}:amd64-${GHCR_VERSION} ${GHCR_REGISTRY}/${GHCR_TAG}:amd64-${GHCR_VERSION}
podman push ${GHCR_REGISTRY}/${GHCR_TAG}:amd64-${GHCR_VERSION}

                                                                                                                                  
# For a multi-platform image (both amd64 + arm64):   


echo "Publishing multi-architecture in github repository"
podman rmi ${GHCR_REGISTRY}/${GHCR_TAG}:${GHCR_VERSION} --force 2>/dev/null || true
podman manifest rm ${GHCR_REGISTRY}/${GHCR_TAG}:${GHCR_VERSION} 2>/dev/null || true
podman manifest create ${GHCR_REGISTRY}/${GHCR_TAG}:${GHCR_VERSION} --amend \
  ${GHCR_REGISTRY}/${GHCR_TAG}:arm64-${GHCR_VERSION} \
  ${GHCR_REGISTRY}/${GHCR_TAG}:amd64-${GHCR_VERSION}
podman manifest push --purge ${GHCR_REGISTRY}/${GHCR_TAG}:${GHCR_VERSION}                                                                    
                                                                                                                                  
# if podman buildx version >/dev/null 2>&1; then
#   podman buildx build \
#     --platform linux/amd64,linux/arm64 \
#     -t ${GHCR_REGISTRY}/${GHCR_TAG}:${GHCR_VERSION} \
#     --push .
# else
#   podman build \
#     --platform linux/amd64,linux/arm64 \
#     --manifest ${GHCR_REGISTRY}/${GHCR_TAG}:${GHCR_VERSION} \
#     -f Dockerfile .

#   podman manifest push --all ${GHCR_REGISTRY}/${GHCR_TAG}:${GHCR_VERSION}
# fi

podman run --rm --name "${GHCR_TAG}" \
    -e LOG_LEVEL="DEBUG" \
    -e TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}" \
    -e PHONE_NUMBER=${PHONE_NUMBER} \
    ${GHCR_REGISTRY}/${GHCR_TAG}:${GHCR_VERSION} 

cd "$current_dir"