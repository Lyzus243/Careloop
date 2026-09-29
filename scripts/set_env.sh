#!/usr/bin/env bash
# Set a secret in .env without it appearing on screen, in your shell history,
# or in a chat transcript.
#
#   ./scripts/set_env.sh META_APP_SECRET
#
set -euo pipefail

KEY="${1:?usage: ./scripts/set_env.sh VARIABLE_NAME}"
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"

[ -f "$ENV_FILE" ] || { echo "No .env at $ENV_FILE"; exit 1; }

printf "Value for %s (hidden): " "$KEY"
read -rs VALUE
printf "\n"
[ -n "$VALUE" ] || { echo "Empty value, nothing changed."; exit 1; }

cp "$ENV_FILE" "$ENV_FILE.bak"

NEW_VALUE="$VALUE" python3 - "$ENV_FILE" "$KEY" <<'PY'
import os, sys
path, key = sys.argv[1], sys.argv[2]
value = os.environ["NEW_VALUE"]
lines = open(path).read().splitlines(keepends=True)
found = False
for i, line in enumerate(lines):
    if line.startswith(f"{key}="):
        lines[i] = f"{key}={value}\n"
        found = True
        break
if not found:
    if lines and not lines[-1].endswith("\n"):
        lines.append("\n")
    lines.append(f"{key}={value}\n")
open(path, "w").write("".join(lines))
PY

echo "$KEY set in .env (previous copy at .env.bak)"
