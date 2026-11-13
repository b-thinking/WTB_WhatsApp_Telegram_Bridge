#!/usr/bin/env bash
# Author: Jose M. Albarran (b-Thinking software)

# Create a pyenv virtual environment and select it locally

# set -x

# This script requires to be executed with 'source'
(return 0 2>/dev/null)
if [ $? -ne 0 ]; then
    echo "You must execute this script with 'source'. Sample:"
    echo "source scripts/$0"
    exit 1
fi


# --------------------------------------------------
# Configuration
# --------------------------------------------------
if [ -n "${BASH_VERSION:-}" ]; then
    SOURCE_FILE="${BASH_SOURCE[0]}"
elif [ -n "${ZSH_VERSION:-}" ]; then
    SOURCE_FILE="${(%):-%x}"
else
    echo "Unsupported shell."
    return 1 2>/dev/null || exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "$SOURCE_FILE")" && pwd)"
WORKSPACE_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${WORKSPACE_DIR}/.env"

echo "SCRIPT_DIR=${SCRIPT_DIR}"
echo "WORKSPACE_DIR=${WORKSPACE_DIR}"
echo "ENV_FILE=${ENV_FILE}"

# --------------------------------------------------
# Load environment variables
# --------------------------------------------------

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: Environment file not found: $ENV_FILE" >&2
    return 1
fi

source "$ENV_FILE"

# --------------------------------------------------
# Check environment variables
# --------------------------------------------------

if [[ -z "${PYTHON_VERSION:-}" ]]; then
    echo "ERROR: PYTHON_VERSION variable required in .env file (e.g. PYTHON_VERSION=3.13)" >&2
    return 1
fi

if [[ -z "${PYENV_ENV_NAME:-}" ]]; then
    echo "ERROR: PYENV_ENV_NAME variable required in .env file (e.g. PYENV_ENV_NAME=myenv)" >&2
    return 1
fi


# PYENV_ENV_NAME="${PYENV_ENV_NAME:-${VIRTUAL_ENV:-bsky}}"
PYTHON_VERSION_GREP="^$(printf '%s' "$PYTHON_VERSION" | sed 's/\./\\./g')(\.[0-9]+)?$"

# Install python PYTHON_VERSION
echo "Checking python $PYTHON_VERSION installation and install if needed"
if ! pyenv versions --bare | grep -Eq "$PYTHON_VERSION_GREP"; then
    pyenv install "$PYTHON_VERSION" || return 1
fi

# Set python PYTHON_VERSION as default (use last available version 3.nn.latest)
PYTHON_VERSION=$(pyenv versions --bare | grep -E "$PYTHON_VERSION_GREP" | sort -V | tail -n 1)
echo "Using Python version: $PYTHON_VERSION"

# Create pyenv virtualenv if needed.
echo "Creating pyenv environment"
if ! pyenv virtualenvs --bare | grep -Fxq "$PYENV_ENV_NAME"; then
    pyenv virtualenv "$PYTHON_VERSION" "$PYENV_ENV_NAME" || return 1
fi

# Select the project environment before installing any library.
# pyenv local "$PYENV_ENV_NAME" || return 1
echo ${PYENV_ENV_NAME} > ${WORKSPACE_DIR}/.python-version
PYTHON_BIN="$(pyenv prefix "$PYENV_ENV_NAME")/bin/python"

# Activate venv environment when pyenv-virtualenv is initialized in the shell.
if ! pyenv activate "$PYENV_ENV_NAME" 2>/dev/null; then
    echo "WARNING: Could not activate pyenv environment" >&2
fi
echo "Active Python: $(pyenv which python)"
"$PYTHON_BIN" -c "import sys; print(f'Python executable: {sys.executable}')"

# Install requirements
echo "Installing requirements in environment"
"$PYTHON_BIN" -m pip install --upgrade pip || return 1
"$PYTHON_BIN" -m pip install -r "${WORKSPACE_DIR}/requirements.txt" || return 1
if [ -f "${WORKSPACE_DIR}/requirements-dev.txt" ]; then
    "$PYTHON_BIN" -m pip install -r "${WORKSPACE_DIR}/requirements-dev.txt" || return 1
fi
