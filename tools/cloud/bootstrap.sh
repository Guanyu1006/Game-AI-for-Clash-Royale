#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
export PIP_CACHE_DIR="$PWD/.cache/cloud/pip" TORCH_HOME="$PWD/.cache/cloud/torch"
export TEMP="$PWD/.tmp/cloud" TMP="$PWD/.tmp/cloud"
mkdir -p "$PIP_CACHE_DIR" "$TORCH_HOME" "$TEMP"
base_python="${CLOUD_PYTHON:-python3.12}"
if ! command -v "$base_python" >/dev/null 2>&1; then base_python=python3; fi
"$base_python" -c 'import sys; assert (3,11)<=sys.version_info[:2]<(3,13), "Cloud profile requires Python3.11/3.12"'
if [ ! -x .venv-cloud/bin/python ]; then "$base_python" -m venv .venv-cloud; fi
py="$PWD/.venv-cloud/bin/python"
"$py" -m pip install --disable-pip-version-check -r requirements-codespaces.txt
if [ "${1:-}" = --ml ]; then
  "$py" -m pip install --disable-pip-version-check -r requirements-codespaces-ml.txt --index-url https://download.pytorch.org/whl/cpu
fi
"$py" tools/cloud/workspace.py restore
"$py" tools/cloud/workspace.py doctor
"$py" tools/cloud/workspace.py test
printf '\nReady. Run: source .venv-cloud/bin/activate\nThen: python tools/cloud/workspace.py serve\n'
