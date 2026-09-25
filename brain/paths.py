# -*- coding: utf-8 -*-
"""ПУТИ ПРОЕКТА — одна точка правды, работает на Windows и на macOS/Linux.

Раньше в двух десятках скриптов был вшит абсолютный путь вида
`C:\\Users\\<пользователь>\\Desktop\\Лид-бот` — при переезде на другую машину всё это ломалось.
Теперь пути вычисляются от расположения самого файла, а корпус ищется так:

  1) переменная окружения  LEADBOT_WEBHOOK_DIR
  2) ключ `jivo_webhook_dir` в config.json
  3) папка «Jivo Webhook» РЯДОМ с проектом (обычный случай: обе на Рабочем столе)
  4) папка «Jivo Webhook» внутри проекта (если корпус положили внутрь)

Использование:
    import paths
    paths.MERGED          # полные склеенные диалоги (основной корпус)
    paths.ANALYSIS        # папка analysis/
    paths.brain_on_path() # добавить brain/ в sys.path из скриптов analysis/
"""
import os
import sys

BRAIN = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BRAIN)                      # корень «Лид-бот»
ANALYSIS = os.path.join(ROOT, "analysis")
UI = os.path.join(ROOT, "ui")
CONFIG_FILE = os.path.join(ROOT, "config.json")


def brain_on_path():
    """Скрипты из analysis/ зовут это первой строкой, чтобы импортировать мозг."""
    if BRAIN not in sys.path:
        sys.path.insert(0, BRAIN)
    return BRAIN


def _config_webhook():
    try:
        import json
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("jivo_webhook_dir") or ""
    except Exception:
        return ""


def webhook_dir():
    """Папка проекта-приёмника «Jivo Webhook» (там лежат сырые вебхуки и корпус)."""
    env = os.environ.get("LEADBOT_WEBHOOK_DIR")
    if env and os.path.isdir(env):
        return env
    cfg = _config_webhook()
    if cfg and os.path.isdir(cfg):
        return cfg
    for cand in (os.path.join(os.path.dirname(ROOT), "Jivo Webhook"),
                 os.path.join(ROOT, "Jivo Webhook")):
        if os.path.isdir(cand):
            return cand
    # ничего не нашли — возвращаем ожидаемое место (ошибка будет понятной)
    return os.path.join(os.path.dirname(ROOT), "Jivo Webhook")


WEBHOOK = webhook_dir()
DATA = os.path.join(WEBHOOK, "data")
MERGED = os.path.join(DATA, "dialogs_merged")     # ПОЛНЫЕ диалоги — основной корпус
DIALOGS = os.path.join(DATA, "dialogs")           # свежие куски от приёмника
RAW = os.path.join(DATA, "raw")                   # сырые вебхуки (источник истины)


def describe():
    """Короткая диагностика — что и где нашлось (для setup/самопроверки)."""
    rows = [("проект", ROOT), ("приёмник Jivo", WEBHOOK), ("корпус (merged)", MERGED),
            ("свежие куски", DIALOGS), ("сырьё raw", RAW)]
    out = []
    for name, p in rows:
        n = ""
        if os.path.isdir(p):
            try:
                n = " (%d файлов)" % len(os.listdir(p))
            except OSError:
                n = ""
            mark = "есть"
        else:
            mark = "НЕТ"
        out.append("%-16s %-4s %s%s" % (name, mark, p, n))
    return "\n".join(out)


if __name__ == "__main__":
    print(describe())
