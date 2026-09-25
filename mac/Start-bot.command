#!/bin/bash
# ЗАПУСК ЛИД-БОТА (Пульт оператора). Двойной щелчок.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
cd "$ROOT" || exit 1

PY="$(cat "$HERE/.python-path" 2>/dev/null)"
[ -x "$PY" ] || PY="$(command -v python3)"
[ -x "$PY" ] || { echo "Python 3 не найден — запусти сначала setup-mac.command"; read -r _; exit 1; }

PORT="${LEADBOT_PORT:-8789}"

# если порт занят прошлым запуском — гасим только СВОЙ сервер (чужие процессы не трогаем)
OLD="$(lsof -ti tcp:"$PORT" 2>/dev/null || true)"
for pid in $OLD; do
  if ps -p "$pid" -o command= 2>/dev/null | grep -q "brain/server.py"; then
    echo "Останавливаю прошлый сервер (pid $pid)"
    kill "$pid" 2>/dev/null
    sleep 1
  fi
done

echo "Запускаю мозг на порту $PORT ..."
export LEADBOT_PORT="$PORT" PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
"$PY" brain/server.py &
SRV=$!
sleep 3

if curl -s "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  echo "Сервер поднялся."
  open "http://localhost:$PORT/relay"
  echo
  echo "Пульт оператора:  http://localhost:$PORT/relay"
  echo "Страница разметки: http://localhost:$PORT/review"
  echo
  echo "ЧТОБЫ ОСТАНОВИТЬ — закрой это окно или нажми Ctrl+C."
  wait $SRV
else
  echo "Сервер не ответил. Смотри ошибку выше."
  read -r _
fi
