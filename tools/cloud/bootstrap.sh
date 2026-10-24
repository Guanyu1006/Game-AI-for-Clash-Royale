#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
install_ml=false
install_research=false
for option in "$@"; do
  case "$option" in
    --ml) install_ml=true ;;
    --research) install_research=true ;;
    -h|--help)
      printf 'Usage: bash tools/cloud/bootstrap.sh [--ml] [--research]\n\n--ml installs CPU Torch; --research installs Parquet/Gymnasium tools.\nFlags can be combined in either order. All files stay inside this repository.\n'
      exit 0 ;;
    *) printf 'Unknown bootstrap option: %s\n' "$option" >&2; exit 2 ;;
  esac
done
export PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
export PIP_CACHE_DIR="$PWD/.cache/cloud/pip" TORCH_HOME="$PWD/.cache/cloud/torch"
export TEMP="$PWD/.tmp/cloud" TMP="$PWD/.tmp/cloud" TMPDIR="$PWD/.tmp/cloud"
export XDG_CACHE_HOME="$PWD/.cache/cloud/xdg" MPLCONFIGDIR="$PWD/.cache/cloud/matplotlib"
mkdir -p "$PIP_CACHE_DIR" "$TORCH_HOME" "$TEMP"
base_python="${CLOUD_PYTHON:-python3.12}"
if ! command -v "$base_python" >/dev/null 2>&1; then base_python=python3; fi
"$base_python" -c 'import sys; assert (3,11)<=sys.version_info[:2]<(3,13), "Cloud profile requires Python3.11/3.12"'
if [ ! -x .venv-cloud/bin/python ]; then "$base_python" -m venv .venv-cloud; fi
py="$PWD/.venv-cloud/bin/python"
"$py" -m pip install --disable-pip-version-check -r requirements-codespaces.txt
if [ "$install_ml" = true ]; then
  "$py" -m pip install --disable-pip-version-check -r requirements-codespaces-ml.txt --index-url https://download.pytorch.org/whl/cpu
fi
if [ "$install_research" = true ]; then
  "$py" -m pip install --disable-pip-version-check -r requirements-codespaces-research.txt
fi
"$py" tools/cloud/workspace.py restore
"$py" tools/cloud/workspace.py doctor
"$py" tools/cloud/workspace.py test
printf '\nReady. Run: .venv-cloud/bin/python tools/cloud/workspace.py doctor\nThen: .venv-cloud/bin/python tools/cloud/workspace.py serve\n'
