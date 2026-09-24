#!/bin/sh
# Install the `onemoretask` command.
#   From anywhere:     curl -fsSL https://raw.githubusercontent.com/astanar01/onemoretask/main/install.sh | sh
#   From a clone:      ./install.sh
# Downloads (or updates) the repo in $ONEMORETASK_DIR (default ~/.onemoretask) unless run from a clone,
# then links bin/onemoretask into ~/.local/bin and adds that folder to PATH if needed.
set -e
REPO=${ONEMORETASK_REPO:-https://github.com/astanar01/onemoretask.git}
BIN=$HOME/.local/bin

for tool in python3 git curl; do
  command -v $tool >/dev/null || { echo "onemoretask needs $tool — install it first."; exit 1; }
done

here=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
if [ -f "$here/server.py" ] && [ -f "$here/bin/onemoretask" ]; then
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
