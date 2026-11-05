#!/usr/bin/env bash
# WolvWealth one-command installer (Linux, macOS, Windows via WSL).
#
#   From a clone:        ./install.sh
#   On a fresh machine:  curl -fsSL https://raw.githubusercontent.com/rramboer/WolvWealth/main/install.sh | bash
#
# What it does:
#   1. Checks for Python 3.10+ and pip (tells you how to get them if missing).
#   2. Clones the repo to ~/WolvWealth if you are not already inside one.
#   3. Installs the package into your user-level global Python (no venv), so
#      `wolvwealth` and `import wolvwealth` work from any directory. The install
#      is editable: the clone stays the single copy of code and data.
#   4. Downloads the price history (2006 -> today, ~40 MB, several minutes).
#      Skip with --no-data; the app will do it on first start instead.
#   5. Tells you how to start it: `wolvwealth serve`.
#
# Options:  --no-data   skip the price download
#           --dev       also install the lint/test tools (contributors)
#
# Node.js is NOT required. The website CSS is committed prebuilt; Node is
# only needed by contributors who change the styling (see CONTRIBUTING.md).

set -Eeuo pipefail

REPO_URL="https://github.com/rramboer/WolvWealth.git"
INSTALL_DIR="${WOLVWEALTH_DIR:-$HOME/WolvWealth}"
WITH_DATA=1
EXTRAS=""

for arg in "$@"; do
  case "$arg" in
    --no-data) WITH_DATA=0 ;;
    --dev) EXTRAS="[dev]" ;;
    -h|--help) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (try --help)"; exit 1 ;;
  esac
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mError:\033[0m %s\n' "$*" >&2; exit 1; }

# ---- 1. Python -------------------------------------------------------------
say "Checking for Python 3.10+"
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PYTHON="$candidate"; break
  fi
done
if [ -z "$PYTHON" ]; then
  case "$(uname -s)" in
    Darwin) HINT="Install it with:  brew install python   (or from https://www.python.org/downloads/)" ;;
    *)      HINT="Install it with your package manager, e.g.:  sudo apt install python3 python3-pip git" ;;
  esac
  fail "Python 3.10 or newer was not found. $HINT"
fi
"$PYTHON" -m pip --version >/dev/null 2>&1 || fail "pip is missing for $PYTHON. On Debian/Ubuntu:  sudo apt install python3-pip"
echo "Using $("$PYTHON" --version) at $(command -v "$PYTHON")"

# ---- 2. Source checkout ------------------------------------------------------
if [ -f "./pyproject.toml" ] && grep -q '^name = "wolvwealth"' ./pyproject.toml 2>/dev/null; then
  INSTALL_DIR="$(pwd)"
  say "Installing from this checkout: $INSTALL_DIR"
else
  command -v git >/dev/null 2>&1 || fail "git was not found. Install it, or download the repo and run ./install.sh inside it."
  if [ -d "$INSTALL_DIR/.git" ]; then
    say "Updating existing checkout in $INSTALL_DIR"
    git -C "$INSTALL_DIR" pull --ff-only
  else
    say "Cloning WolvWealth into $INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi
  cd "$INSTALL_DIR"
fi

# ---- 3. pip install (global per-user site, no venv) -------------------------
say "Installing the wolvwealth package for your user (no venv)"
# --break-system-packages is needed on Debian/Ubuntu/Homebrew Pythons (PEP 668)
# and unknown to older pips, so try with it first and fall back without it.
if ! "$PYTHON" -m pip install --quiet --user --break-system-packages -e ".${EXTRAS}" 2>/dev/null; then
  "$PYTHON" -m pip install --quiet --user -e ".${EXTRAS}"
fi

# The `wolvwealth` command lands in the user script dir; make sure it is on PATH.
USER_BIN="$("$PYTHON" -c 'import sysconfig; print(sysconfig.get_path("scripts", f"{__import__("os").name}_user"))')"
case ":$PATH:" in
  *":$USER_BIN:"*) ;;
  *)
    for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
      if [ -f "$rc" ] && ! grep -q "WolvWealth installer" "$rc"; then
        printf '\n# Added by the WolvWealth installer so the `wolvwealth` command is found\nexport PATH="%s:$PATH"\n' "$USER_BIN" >> "$rc"
        echo "Added $USER_BIN to PATH in $rc"
      fi
    done
    export PATH="$USER_BIN:$PATH"
    PATH_NOTE="  (open a new terminal first, or run:  export PATH=\"$USER_BIN:\$PATH\")"
    ;;
esac

# ---- 4. Price history --------------------------------------------------------
if [ "$WITH_DATA" = 1 ]; then
  say "Downloading price history (2006 -> today, about 40 MB; this takes several minutes)"
  if ! "$PYTHON" -m wolvwealth refresh-data; then
    echo "Price download failed (offline, or Yahoo is throttling). The app will retry on first start."
  fi
else
  say "Skipping price download (--no-data); the app downloads it on first start"
fi

# ---- 5. Done -----------------------------------------------------------------
say "Installed."
cat <<MSG

  Start it:    wolvwealth serve${PATH_NOTE:-}
  Then open:   http://localhost:8000/   (opens automatically)

  Using Claude Desktop, Claude Code, or Cursor?  wolvwealth mcp --config
  prints the setup for using WolvWealth as tools there (details: MCP.md).

  Everything stays on this computer under $INSTALL_DIR/var/.
  To update later, run this installer again.
MSG
