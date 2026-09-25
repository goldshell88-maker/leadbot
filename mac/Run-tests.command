#!/bin/bash
# ЛОКАЛЬНЫЕ ТЕСТЫ (0 токенов, шлюз НЕ дёргается) — быстрая проверка, что всё цело после переезда.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
cd "$ROOT" || exit 1
PY="$(cat "$HERE/.python-path" 2>/dev/null)"
[ -x "$PY" ] || PY="$(command -v python3)"
export PYTHONIOENCODING=utf-8

echo "=== пути ==="
"$PY" brain/paths.py
echo
echo "=== гарды роутеров (48 юнитов) ==="
"$PY" analysis/test_router_guards.py
echo
echo "=== обязательные озвучки цен ==="
"$PY" analysis/test_callouts.py | tail -3
echo
echo "=== фильтр мусора ==="
"$PY" analysis/test_junk.py | tail -2
echo
echo "Тесты со шлюзом (hardcore/stress/accept) запускай отдельно: bash analysis/_run_all.sh"
echo "Окно можно закрыть."
read -r _
