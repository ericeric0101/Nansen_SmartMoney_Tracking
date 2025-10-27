#!/usr/bin/env bash
set -euo pipefail

python -m nansen_sm_collector run-once --no-use-mock
