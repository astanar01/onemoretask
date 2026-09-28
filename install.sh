#!/bin/sh
# Install the `onemoretask` command.
#   From anywhere:     curl -fsSL https://raw.githubusercontent.com/astanar01/onemoretask/main/install.sh | sh
#   From a clone:      ./install.sh
# Downloads (or updates) the repo in $ONEMORETASK_DIR (default ~/.onemoretask) unless run from a clone,
# then links bin/onemoretask into ~/.local/bin and adds that folder to PATH if needed.
# Also copies the bundled task-observer Claude skill to ~/.claude/skills (skip: ONEMORETASK_NO_OBSERVER=1),
# and makes a double-click shortcut that starts the board (skip: ONEMORETASK_NO_SHORTCUT=1):
# ~/Desktop/OneMoreTask.command + ~/Applications/OneMoreTask.app on macOS, a .desktop launcher on Linux.
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
# of it (marked by FORKED_FROM) unless a file in it changed since (checked against .install-sums). A user's own
# copy is left alone. Never fails the install.
if [ "${ONEMORETASK_NO_OBSERVER:-}" != 1 ]; then
  obs=${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/task-observer
  # .DS_Store: Finder adds it when the folder is opened, which is not an edit.
  obs_sums() { (cd "$obs" && find . -type f ! -name .install-sums ! -name .DS_Store -exec cksum {} + | LC_ALL=C sort); }
  if [ -f "$obs/SKILL.md" ] && [ ! -f "$obs/FORKED_FROM" ]; then
    echo "Kept your own task-observer skill in $obs as is."
  elif [ ! -f "$obs/SKILL.md" ] && [ -d "$obs" ] && [ -n "$(ls -A "$obs" 2>/dev/null)" ]; then
    echo "Note: $obs has files but no SKILL.md — left alone, task-observer skill not installed."
  elif [ -f "$obs/.install-sums" ] && [ "$(obs_sums)" != "$(cat "$obs/.install-sums")" ]; then
    echo "Kept the task-observer skill in $obs: it was edited since the last install."
    echo "  To get the new version instead, delete that folder and run the install again."
  else
    # Copy next to it first and swap only once the copy is whole, so a failed copy keeps the old skill.
    new=$obs.new-$$ old=$obs.old-$$
    if mkdir -p "$new" && cp -R "$dir/skills/task-observer/." "$new/" \
        && (obs=$new; obs_sums > "$new/.install-sums") \
        && { [ ! -e "$obs" ] || mv "$obs" "$old"; } && mv "$new" "$obs"; then
      rm -rf "$old"
      echo "Installed the task-observer skill in $obs"
    else
      [ -e "$obs" ] || [ ! -e "$old" ] || mv "$old" "$obs"
      rm -rf "$new"
      echo "Note: could not install the task-observer skill in $obs — skipped, the old copy is kept."
    fi
  fi
fi

# Double-click shortcut. Runs bin/onemoretask by absolute path: a GUI launch may not have ~/.local/bin on PATH.
# Called only inside `if`, where set -e is off, so every step is chained and a failure never stops the install.
shortcut_mac() {
  [ -d "$HOME/Desktop" ] || return 1
  sc=$HOME/Desktop/OneMoreTask.command
  qdir=$(printf '%s' "$dir/bin/onemoretask" | sed "s/'/'\\\\''/g")
  printf '%s\n' '#!/bin/sh' '# Starts the OneMoreTask board. Close this window to stop it.' \
    'PATH="$PATH:/opt/homebrew/bin:/usr/local/bin"' "exec '$qdir'" > "$sc" && chmod +x "$sc" || return 1
  where=$sc
  # The .app is for Spotlight/Launchpad. It opens its own copy of the .command in Terminal so the server
  # window stays visible.
  app=$HOME/Applications/OneMoreTask.app
  rm -rf "$app" && mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources" \
    && cp "$sc" "$app/Contents/Resources/OneMoreTask.command" \
    && printf '%s\n' '#!/bin/sh' 'exec open -a Terminal "$(dirname "$0")/../Resources/OneMoreTask.command"' \
      > "$app/Contents/MacOS/OneMoreTask" \
    && chmod +x "$app/Contents/MacOS/OneMoreTask" \
    && printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>' \
      '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' \
      '<plist version="1.0"><dict>' \
      '<key>CFBundleExecutable</key><string>OneMoreTask</string>' \
      '<key>CFBundleIdentifier</key><string>io.github.astanar01.onemoretask</string>' \
      '<key>CFBundleName</key><string>OneMoreTask</string>' \
      '<key>CFBundlePackageType</key><string>APPL</string>' \
      '</dict></plist>' > "$app/Contents/Info.plist" \
    && where="$where and $app"
  return 0
}
shortcut_linux() {
  apps=${XDG_DATA_HOME:-$HOME/.local/share}/applications
  # Exec= quoting: escape \ " ` $ inside the double quotes, then double every \ again (the .desktop string
  # escape), and % becomes %%.
  qdir=$(printf '%s' "$dir/bin/onemoretask" | sed -e 's/[\\"`$]/\\&/g' -e 's/\\/\\\\/g' -e 's/%/%%/g')
  mkdir -p "$apps" && printf '%s\n' '[Desktop Entry]' 'Type=Application' 'Name=OneMoreTask' \
    'Comment=Start the OneMoreTask board' "Exec=\"$qdir\"" 'Terminal=true' 'Icon=utilities-terminal' \
    'Categories=Development;' > "$apps/onemoretask.desktop" || return 1
  where=$apps/onemoretask.desktop
  desk=$(xdg-user-dir DESKTOP 2>/dev/null) || desk=
  [ -n "$desk" ] && [ "$desk" != "$HOME" ] && [ -d "$desk" ] || desk=$HOME/Desktop
  if [ -d "$desk" ] && cp "$apps/onemoretask.desktop" "$desk/" && chmod +x "$desk/onemoretask.desktop"; then
    # GNOME will not run a desktop launcher until it is marked trusted.
    gio set "$desk/onemoretask.desktop" metadata::trusted true >/dev/null 2>&1 || true
    where="$where and $desk/onemoretask.desktop"
  fi
  return 0
}
if [ "${ONEMORETASK_NO_SHORTCUT:-}" != 1 ]; then
  case $(uname -s 2>/dev/null) in
    Darwin) mk=shortcut_mac ;;
    *) mk=shortcut_linux ;;
  esac
  if $mk 2>/dev/null; then
    echo "Made a double-click shortcut: $where"
  else
    echo "Note: could not make a double-click shortcut — skipped."
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
