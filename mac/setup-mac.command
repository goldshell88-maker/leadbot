#!/bin/bash
# УСТАНОВКА «ЛИД-БОТА» НА MAC — запусти двойным щелчком.
# Ставит Homebrew (если нет), Python 3 и Pillow; проверяет пути и корпус;
# создаёт ярлыки запуска и ежечасную склейку диалогов. Ничего не удаляет.
set -u

BOLD=$'\033[1m'; GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'
say()  { printf "%s\n" "$*"; }
ok()   { printf "%s✔%s %s\n" "$GREEN" "$OFF" "$*"; }
warn() { printf "%s!%s %s\n" "$YELLOW" "$OFF" "$*"; }
die()  { printf "%s✘ %s%s\n" "$RED" "$*" "$OFF"; printf "\nОкно можно закрыть.\n"; exit 1; }
head() { printf "\n%s== %s ==%s\n" "$BOLD" "$*" "$OFF"; }

# Каталог проекта = родитель папки mac/, где лежит этот файл
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
cd "$ROOT" || die "не могу перейти в $ROOT"

printf "%s\n" "$BOLD"
say "  ЛИД-БОТ — установка на Mac"
printf "%s\n" "$OFF"
say "  Проект: $ROOT"

# ---------------------------------------------------------------- 1. Homebrew
head "1/6 Homebrew"
if command -v brew >/dev/null 2>&1; then
  ok "Homebrew уже стоит ($(brew --version | head -1))"
else
  warn "Homebrew не найден — ставлю (попросит пароль от Mac, это нормально)"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
    || die "не удалось поставить Homebrew"
  # добавляем brew в PATH для Apple Silicon и Intel
  for p in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$p" ] && eval "$("$p" shellenv)"
  done
  command -v brew >/dev/null 2>&1 || die "Homebrew поставился, но не виден — перезапусти терминал и запусти снова"
  ok "Homebrew установлен"
fi

# ---------------------------------------------------------------- 2. Python 3
head "2/6 Python 3"
PY=""
for cand in python3.13 python3.12 python3.11 python3; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$(command -v "$cand")"; break; fi
done
if [ -z "$PY" ]; then
  warn "Python 3 не найден — ставлю через brew"
  brew install python@3.12 || die "не удалось поставить Python"
  PY="$(command -v python3)"
fi
PYVER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"
case "$PYVER" in
  3.9|3.10|3.11|3.12|3.13|3.14) ok "Python $PYVER — $PY" ;;
  *) warn "Python $PYVER старый, ставлю свежий"; brew install python@3.12 && PY="$(command -v python3)"
     ok "Python $("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" ;;
esac
# мозг работает на голой stdlib — сохраняем путь к интерпретатору для ярлыков
echo "$PY" > "$ROOT/mac/.python-path"

# ---------------------------------------------------------------- 3. Pillow
head "3/6 Pillow (сжатие фото перед отправкой в модель)"
if "$PY" -c "import PIL" >/dev/null 2>&1; then
  ok "Pillow уже стоит"
else
  "$PY" -m pip install --user --quiet --disable-pip-version-check Pillow 2>/dev/null \
    || "$PY" -m pip install --break-system-packages --user --quiet Pillow 2>/dev/null \
    || warn "Pillow не встал — бот будет работать, но фото пойдут без сжатия (дороже)"
  "$PY" -c "import PIL" >/dev/null 2>&1 && ok "Pillow установлен"
fi

# ---------------------------------------------------------------- 4. Проверка проекта
head "4/6 Файлы проекта и корпус"
[ -f "$ROOT/brain/server.py" ] || die "нет brain/server.py — папка проекта неполная"
[ -f "$ROOT/config.json" ]     || die "нет config.json — скопируй его с рабочей машины (там токен)"
ok "мозг и config.json на месте"
"$PY" "$ROOT/brain/paths.py" || die "не смог определить пути"

MERGED="$("$PY" -c "import sys; sys.path.insert(0,'$ROOT/brain'); import paths; print(paths.MERGED)")"
if [ -d "$MERGED" ]; then
  N=$(ls -1 "$MERGED" 2>/dev/null | wc -l | tr -d ' ')
  ok "корпус найден: $N диалогов"
else
  warn "корпус не найден по пути $MERGED"
  warn "положи папку «Jivo Webhook» рядом с «Лид-бот» — или задай LEADBOT_WEBHOOK_DIR"
  warn "бот запустится и без корпуса, но пересборка RAG и анализ работать не будут"
fi

# ---------------------------------------------------------------- 5. Ярлыки
head "5/6 Ярлыки запуска"
chmod +x "$ROOT/mac/"*.command 2>/dev/null
ok "Start-bot.command — запуск Пульта"
ok "Merge-dialogs.command — ручная склейка диалогов"
ok "Run-tests.command — локальные тесты (без шлюза)"

# ежечасная склейка (аналог задачи планировщика Windows)
head "6/6 Ежечасная склейка диалогов"
PLIST="$HOME/Library/LaunchAgents/ru.leadbot.merge.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ru.leadbot.merge</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$ROOT/analysis/merge_dialogs.py</string>
  </array>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$ROOT/mac/merge.log</string>
  <key>StandardErrorPath</key><string>$ROOT/mac/merge.log</string>
</dict>
</plist>
PLISTEOF
launchctl unload "$PLIST" >/dev/null 2>&1
if launchctl load "$PLIST" >/dev/null 2>&1; then
  ok "склейка будет запускаться каждый час (лог: mac/merge.log)"
else
  warn "не удалось включить автосклейку — запускай Merge-dialogs.command вручную"
fi

printf "\n%s================================================%s\n" "$BOLD" "$OFF"
ok "ГОТОВО"
say ""
say "Дальше:"
say "  1. Запусти  mac/Start-bot.command   — поднимет сервер"
say "  2. Открой   http://localhost:8789/relay   — Пульт оператора"
say ""
say "Если переходишь на прямой API Anthropic вместо шлюза — в config.json:"
say "  \"gateway_base\": \"https://api.anthropic.com\","
say "  \"auth_token\":   \"sk-ant-...\""
say "Стиль авторизации переключится сам (x-api-key). Можно и через окружение:"
say "  export LEADBOT_API_BASE=https://api.anthropic.com"
say "  export LEADBOT_API_KEY=sk-ant-..."
say ""
printf "Окно можно закрыть.\n"
