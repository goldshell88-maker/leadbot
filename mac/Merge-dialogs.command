#!/bin/bash
# СКЛЕЙКА ДИАЛОГОВ вручную (обычно идёт сама раз в час через launchd).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
cd "$ROOT" || exit 1
PY="$(cat "$HERE/.python-path" 2>/dev/null)"
[ -x "$PY" ] || PY="$(command -v python3)"
export PYTHONIOENCODING=utf-8
"$PY" analysis/merge_dialogs.py
echo
echo "Готово. Окно можно закрыть."
read -r _
