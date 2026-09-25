# -*- coding: utf-8 -*-
"""Предохранитель по сети: массовый прогон не перемалывает мёртвые запросы.

ПОВОД 26.08. Посреди полного прогона пропал DNS до api.anthropic.com. Набор
`test_primary_mass` не «завис» — он медленно умирал: 100 вызовов в два потока, у
каждого свои три попытки с паузами, и это ровно двенадцать минут перемалывания
заведомо мёртвых запросов. Полный прогон вместо десяти минут занял сорок четыре.

⚠ ТАЙМАУТ ТУТ НИ ПРИ ЧЁМ, И ЭТО ГЛАВНОЕ. Таймаут есть — 90 секунд на запрос, — и он
НЕ срабатывает: запросы падают быстро, просто их сто. Чинить надо не время одного
вызова, а бессмысленность остальных: если сети нет, девяносто седьмой вызов не
покажет ничего сверх третьего.

⚠ СЧИТАЕМ ПОДРЯД, А НЕ ВСЕГО. Одиночный сетевой сбой на сотне вызовов — норма жизни
(429, обрыв, таймаут провайдера). Ронять из-за него прогон нельзя: тогда предохранитель
сам станет источником ложных провалов.

Запуск: uv run python analysis/test_network_breaker.py
Офлайн, 0 токенов.
"""
import importlib.util
import io
import os
import sys

# ⚠ stdout не перенастраиваем: это делает сам набор при импорте, а две обёртки
# над одним буфером закрывают его, когда первую собирает сборщик мусора.
КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ФАЙЛ = os.path.join(КОРЕНЬ, "analysis", "test_primary_mass.py")

ошибки = []


def проверь(имя, условие, чем=""):
    print(("  ✓ " if условие else "  ✗ ") + имя + (("  → " + str(чем)[:70]) if not условие else ""))
    if not условие:
        ошибки.append(имя)


# ⚠ ГРУЗИМ МОДУЛЬ, НО НЕ ЗАПУСКАЕМ ЕГО. У набора вся работа под `if __name__`, поэтому
# импорт безопасен и не стоит ни одного токена.
_спец = importlib.util.spec_from_file_location("_pm", ФАЙЛ)
_pm = importlib.util.module_from_spec(_спец)
sys.modules["_pm"] = _pm
_спец.loader.exec_module(_pm)

print("детектор сетевой ошибки")
СЕТЕВЫЕ = [
    "Сеть: [Errno 8] nodename nor servname provided, or not known",
    "Temporary failure in name resolution",
    "Connection refused",
    "Network is unreachable",
    "The read operation timed out",
]
for т in СЕТЕВЫЕ:
    проверь("сетевая: %s" % т[:44], bool(_pm._СЕТЕВАЯ.search(т)))
НЕ_СЕТЕВЫЕ = [
    "HTTP 400: bad request",
    "KeyError: 'reply'",
    "TypeError: 'set' object is not subscriptable",
    "HTTP 529: overloaded",
]
for т in НЕ_СЕТЕВЫЕ:
    проверь("НЕ сетевая: %s" % т[:44], not _pm._СЕТЕВАЯ.search(т))

print("счётчик считает ПОДРЯД, а не всего")
_pm._сеть["подряд"] = 0
_pm.server = type("_", (), {"run_relay": staticmethod(
    lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Сеть: nodename nor servname provided")))})()
for _ in range(2):
    _pm.check_one({"text": "стиралка не отжимает"}, "p1")
проверь("два сетевых подряд — счётчик 2", _pm._сеть["подряд"] == 2, _pm._сеть["подряд"])

_pm.server = type("_", (), {"run_relay": staticmethod(lambda *a, **k: {"reply": "да, помогу. Куда подъехать?"})})()
_pm.check_one({"text": "стиралка не отжимает"}, "p1")
проверь("успешный вызов обнуляет счётчик", _pm._сеть["подряд"] == 0, _pm._сеть["подряд"])

_pm.server = type("_", (), {"run_relay": staticmethod(
    lambda *a, **k: (_ for _ in ()).throw(RuntimeError("KeyError: 'reply'")))})()
_pm.check_one({"text": "стиралка не отжимает"}, "p1")
проверь("НЕсетевая ошибка счётчик не растит", _pm._сеть["подряд"] == 0, _pm._сеть["подряд"])

print("после порога вызовы больше не делаются")
_pm._сеть["подряд"] = 0
звонков = {"n": 0}


def _падает(*a, **k):
    звонков["n"] += 1
    raise RuntimeError("Сеть: nodename nor servname provided")


_pm.server = type("_", (), {"run_relay": staticmethod(_падает)})()
for _ in range(20):
    r = _pm.check_one({"text": "стиралка не отжимает"}, "p1")
проверь("сделано ровно %d вызовов, остальные пропущены" % _pm._ПОДРЯД_ДО_ОСТАНОВКИ,
        звонков["n"] == _pm._ПОДРЯД_ДО_ОСТАНОВКИ, звонков["n"])
проверь("пропущенные помечены флагом network", r["flags"] == ["network"], r["flags"])
проверь("порог не единица (одиночный сбой прогон не роняет)", _pm._ПОДРЯД_ДО_ОСТАНОВКИ >= 3)

print()
if ошибки:
    print("ПРОВАЛЕНО: %d" % len(ошибки))
    for e in ошибки:
        print("  -", e)
    sys.exit(1)
print("Все проверки пройдены")
