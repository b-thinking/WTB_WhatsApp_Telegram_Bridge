#!/usr/bin/env bash

# Requires omlx in path or alias to pyenv environment
# Sample: alias omlx="$HOME/.pyenv/versions/omlx/bin/omlx"
OMLX_MODEL="Qwen3.8-27B-oQ4e-mtp"

omlx launch claude \
  --model ${OMLX_MODEL} \
  --opus ${OMLX_MODEL} \
  --sonnet ${OMLX_MODEL} \
  --haiku ${OMLX_MODEL}