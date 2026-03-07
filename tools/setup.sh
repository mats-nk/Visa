#!/usr/bin/env bash
# ============================================================
#  setup.sh  –  Create a Python venv and install dependencies
#  for weather_mqtt.py
# ============================================================

set -euo pipefail

VENV_DIR=".venv"
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=8
REQUIREMENTS="paho-mqtt requests pyyaml"

# ── Colours ──────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'
YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
die()     { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }

echo -e "${BOLD}================================================${RESET}"
echo -e "${BOLD}  weather_mqtt  –  environment setup            ${RESET}"
echo -e "${BOLD}================================================${RESET}"
echo

# ── 1. Find a suitable Python interpreter ────────────────────
info "Looking for Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ ..."

PYTHON=""
for candidate in python3 python python3.13 python3.12 python3.11 python3.10 python3.9 python3.8; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c \
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null) || continue
        major=${ver%%.*}
        minor=${ver##*.}
        if [[ "$major" -gt "$PYTHON_MIN_MAJOR" ]] || \
           [[ "$major" -eq "$PYTHON_MIN_MAJOR" && "$minor" -ge "$PYTHON_MIN_MINOR" ]]; then
            PYTHON="$candidate"
            break
        fi
    fi
done

[[ -z "$PYTHON" ]] && die "No Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ found. Please install Python first."

PYTHON_VERSION=$("$PYTHON" -c "import sys; print(sys.version.split()[0])")
success "Using $PYTHON  (version $PYTHON_VERSION)"

# ── 2. Create virtual environment ────────────────────────────
if [[ -d "$VENV_DIR" ]]; then
    warn "Virtual environment '$VENV_DIR' already exists – skipping creation."
    warn "Delete it and re-run this script to start fresh."
else
    info "Creating virtual environment in '$VENV_DIR' ..."
    "$PYTHON" -m venv "$VENV_DIR"
    success "Virtual environment created."
fi

# ── 3. Activate venv ─────────────────────────────────────────
info "Activating virtual environment ..."
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
success "Activated: $(python --version)"

# ── 4. Upgrade pip ───────────────────────────────────────────
info "Upgrading pip ..."
python -m pip install --upgrade pip --quiet
success "pip $(pip --version | awk '{print $2}')"

# ── 5. Install requirements ──────────────────────────────────
if [[ -f "requirements.txt" ]]; then
    info "Installing from requirements.txt ..."
    pip install -r requirements.txt --quiet
else
    info "Installing packages: ${REQUIREMENTS} ..."
    # shellcheck disable=SC2086
    pip install $REQUIREMENTS --quiet
fi
success "All packages installed."

# ── 6. Verify imports ────────────────────────────────────────
info "Verifying imports ..."
python - <<'EOF'
import importlib, sys
packages = {"paho.mqtt.client": "paho-mqtt", "requests": "requests", "yaml": "pyyaml"}
all_ok = True
for module, pkg in packages.items():
    try:
        m = importlib.import_module(module)
        ver = getattr(m, "__version__", "n/a")
        print(f"  ✓  {pkg:<12}  ({ver})")
    except ImportError:
        print(f"  ✗  {pkg}  MISSING", file=sys.stderr)
        all_ok = False
sys.exit(0 if all_ok else 1)
EOF
success "All imports OK."

# ── 7. Write requirements.txt if absent ──────────────────────
if [[ ! -f "requirements.txt" ]]; then
    info "Saving requirements.txt ..."
    pip freeze | grep -iE "paho|requests|pyyaml" > requirements.txt
    success "requirements.txt written."
fi

# ── 8. Smoke test ────────────────────────────────────────────
echo
info "Running smoke test (dry-run, single fetch) ..."
echo -e "${BOLD}------------------------------------------------${RESET}"
python weather_mqtt.py --dry-run --once
echo -e "${BOLD}------------------------------------------------${RESET}"

# ── Done ─────────────────────────────────────────────────────
echo
echo -e "${BOLD}${GREEN}Setup complete!${RESET}"
echo
echo -e "  Next time, activate the environment with:"
echo -e "    ${CYAN}source ${VENV_DIR}/bin/activate${RESET}"
echo
echo -e "  Then run the weather publisher:"
echo -e "    ${CYAN}python weather_mqtt.py${RESET}"
echo
