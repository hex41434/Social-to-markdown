#!/bin/bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_app="$project_dir/launcher/Social Saves.app"
destination_app="${1:-$HOME/Desktop/AiFa Social.app}"

if [[ ! -d "$source_app" ]]; then
  echo "Launcher source not found: $source_app" >&2
  exit 1
fi

/usr/bin/ditto "$source_app" "$destination_app"
# Store this checkout's location only in the installed, untracked copy.
printf '%s\n' "$project_dir" > "$destination_app/Contents/Resources/project-dir.txt"
/bin/chmod +x "$destination_app/Contents/MacOS/Social Saves"
/usr/bin/touch "$destination_app"
/usr/bin/open -R "$destination_app"
echo "Installed clickable app: $destination_app"
