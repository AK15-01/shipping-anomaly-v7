#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-8503}"
APP_FILE="app.py"
VENV_DIR=".venv"
VENV_PY="${VENV_DIR}/bin/python"
SELECTED_PYTHON=""
SELECTED_VERSION=""
SELECTED_MINOR=""
REUSE_VENV="false"

python_is_compatible() {
  "$1" -c 'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 15) else 1)' \
    >/dev/null 2>&1
}

python_version() {
  "$1" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")'
}

python_minor() {
  "$1" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
}

find_compatible_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3.10 python3.14 python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1 && python_is_compatible "${candidate}"; then
      SELECTED_PYTHON="${candidate}"
      SELECTED_VERSION="$(python_version "${candidate}")"
      SELECTED_MINOR="$(python_minor "${candidate}")"
      return 0
    fi
  done
  return 1
}

echo "======================================================"
echo "Starting: Shipping Anomaly Detection"
echo "Supported Python: 3.10 - 3.14"
echo "Entry point: ${APP_FILE}"
echo "Local URL: http://localhost:${PORT}"
echo "======================================================"
echo

if [[ ! -f "${APP_FILE}" ]]; then
  echo "[Error] ${APP_FILE} was not found. Run this launcher from the project root."
  exit 1
fi
if [[ ! -f requirements.txt ]]; then
  echo "[Error] requirements.txt was not found."
  exit 1
fi

echo "[1/5] Checking the existing virtual environment..."
if [[ -x "${VENV_PY}" ]]; then
  VENV_VERSION="$(python_version "${VENV_PY}" 2>/dev/null || true)"
  if python_is_compatible "${VENV_PY}"; then
    REUSE_VENV="true"
    echo "Compatible virtual environment detected: Python ${VENV_VERSION}"
    echo "Reusing ${VENV_DIR}; it will not be recreated."
  else
    if [[ -n "${VENV_VERSION}" ]]; then
      echo "Old virtual environment detected: Python ${VENV_VERSION}"
    else
      echo "The existing virtual environment is damaged or cannot be executed."
    fi
    echo "This environment is incompatible with the project."
  fi
elif [[ -e "${VENV_DIR}" ]]; then
  echo "An incomplete virtual environment was detected and will be rebuilt."
else
  echo "No existing virtual environment was detected."
fi

if [[ "${REUSE_VENV}" != "true" ]]; then
  echo
  echo "[2/5] Finding a compatible Python interpreter..."
  if ! find_compatible_python; then
    echo
    echo "[Error] The available Python version is too old, or no compatible interpreter can run."
    echo
    echo "This project requires Python 3.10 - 3.14."
    echo "Python 3.12 or Python 3.13 is recommended."
    echo
    echo "Install a compatible Python version and run this launcher again."
    exit 1
  fi

  echo "Compatible Python detected: ${SELECTED_VERSION}"
  echo "Python ${SELECTED_MINOR} will be used to create the environment."

  if [[ -e "${VENV_DIR}" ]]; then
    echo "Rebuilding the virtual environment..."
    if ! rm -rf -- "${VENV_DIR}"; then
      echo "[Error] Could not remove ${VENV_DIR}. Close any process using it, delete it manually, and retry."
      exit 1
    fi
    if [[ -e "${VENV_DIR}" ]]; then
      echo "[Error] ${VENV_DIR} still exists after cleanup. Delete it manually and retry."
      exit 1
    fi
  else
    echo "Creating the virtual environment..."
  fi

  if ! "${SELECTED_PYTHON}" -m venv "${VENV_DIR}"; then
    echo "[Error] Failed to create ${VENV_DIR} with Python ${SELECTED_VERSION}."
    echo "Check that the Python venv module is installed and the project directory is writable."
    exit 1
  fi
fi

echo
echo "[3/5] Verifying the virtual environment Python..."
VENV_VERSION="$(python_version "${VENV_PY}" 2>/dev/null || true)"
if ! python_is_compatible "${VENV_PY}"; then
  echo "[Error] Unsupported virtual environment Python: ${VENV_VERSION:-unknown}"
  echo "This project requires Python 3.10 - 3.14."
  exit 1
fi
echo "Virtual environment: Python ${VENV_VERSION}"

echo
echo "[4/5] Installing/checking production dependencies..."
"${VENV_PY}" -m pip install --upgrade pip
"${VENV_PY}" -m pip install -r requirements.txt

echo
echo "[5/5] Starting Streamlit..."
echo "If the browser does not open, visit: http://localhost:${PORT}"
echo
exec "${VENV_PY}" -m streamlit run "${APP_FILE}" \
  --server.port "${PORT}" \
  --server.address localhost
