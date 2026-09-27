#!/bin/sh
# Install the `onemoretask` command.
#   From anywhere:     curl -fsSL https://raw.githubusercontent.com/astanar01/onemoretask/main/install.sh | sh
#   From a clone:      ./install.sh
# Downloads (or updates) the repo in $ONEMORETASK_DIR (default ~/.onemoretask) unless run from a clone,
# then links bin/onemoretask into ~/.local/bin and adds that folder to PATH if needed.
# Also copies the bundled task-observer Claude skill to ~/.claude/skills (skip: ONEMORETASK_NO_OBSERVER=1).
set -e
case $(uname -s 2>/dev/null) in
  MINGW*|MSYS*|CYGWIN*)
    echo "On Windows, install from PowerShell instead:"
    echo "  irm https://raw.githubusercontent.com/astanar01/onemoretask/main/install.ps1 | iex"
    exit 1 ;;
esac
REPO=${ONEMORETASK_REPO:-https://github.com/astanar01/onemoretask.git}
BIN=$HOME/.local/bin

for tool in python3 git; do
  command -v $tool >/dev/null || { echo "onemoretask needs $tool — install it first."; exit 1; }
done
python3 -c 'import sys; sys.exit(sys.version_info < (3, 7))' || { echo "onemoretask needs Python 3.7 or newer."; exit 1; }

here=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
if [ -f "$here/server.py" ] && [ -f "$here/launch.py" ] && [ -f "$here/bin/onemoretask" ]; then
  dir=$here
else
  dir=${ONEMORETASK_DIR:-$HOME/.onemoretask}
  if [ -d "$dir/.git" ]; then
    echo "Updating $dir"
    git -C "$dir" pull --ff-only -q
  else
    echo "Downloading to $dir"
    git clone -q "$REPO" "$dir"
  fi
fi

mkdir -p "$BIN"
chmod +x "$dir/bin/onemoretask"
ln -sf "$dir/bin/onemoretask" "$BIN/onemoretask"
echo "Linked $BIN/onemoretask"

# task-observer skill: our fork in skills/task-observer is copied in when missing, and replaces an earlier copy
# of it (marked by FORKED_FROM). A user's own copy is left alone. Never fails the install.
if [ "${ONEMORETASK_NO_OBSERVER:-}" != 1 ]; then
  obs=${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/task-observer
  if [ -f "$obs/SKILL.md" ] && [ ! -f "$obs/FORKED_FROM" ]; then
    echo "Kept your own task-observer skill in $obs as is."
  elif [ ! -f "$obs/SKILL.md" ] && [ -d "$obs" ] && [ -n "$(ls -A "$obs" 2>/dev/null)" ]; then
    echo "Note: $obs has files but no SKILL.md — left alone, task-observer skill not installed."
  elif mkdir -p "$obs" && rm -rf "$obs/references" "$obs/scripts" && cp -R "$dir/skills/task-observer/." "$obs/"; then
    echo "Installed the task-observer skill in $obs"
  else
    echo "Note: could not install the task-observer skill in $obs — skipped."
  fi
fi

case ":$PATH:" in
  *":$BIN:"*) ;;
  *)
    case ${SHELL##*/} in
      zsh) rc=$HOME/.zshrc ;;
      bash) rc=$HOME/.bashrc ;;
      *) rc=$HOME/.profile ;;
    esac
    line='export PATH="$HOME/.local/bin:$PATH"'
    grep -qsF "$line" "$rc" || printf '\n%s\n' "$line" >> "$rc"
    echo "Added ~/.local/bin to PATH in $rc — open a new terminal first."
    ;;
esac

command -v claude >/dev/null || echo "Note: Send to Claude needs the Claude Code CLI (claude) on PATH."
echo "Done. Run: onemoretask"
