# -*- coding: utf-8 -*-
"""Стилевые запреты снимаются в ЕДИНСТВЕННОЙ точке выхода, а не в середине конвейера.

НАХОДКА ЭТАЛОНА 26.08. `_no_dash` стоял внутри постпроцессора — ДО того, как воронка
дописывает свои пулы. А в пулах тире было:

    prefilter: «Оставьте номер — согласую время и подтвержу»
               «И номер оставьте — так проще договориться»
    server:    «Стоимость зависит от объёма — гляну на месте и сразу скажу»

Запрет тире — решение владельца от 30.07 (живые мастера их не пишут, тире в каждой
реплике выдаёт бота). На эталонной выборке тире доехало до клиента в 4 ходах из 45.

⚠ ПРАВИЛЬНОЕ МЕСТО — `_contract`, ЕДИНСТВЕННЫЙ ВЫХОД ВСЕГО БОТА. Чинить пулы по одному
бессмысленно: их пишут заново каждую неделю, и следующий автор снова поставит тире.
Правило должно стоять там, где текст уходит наружу, — тогда новый пул получает его даром.

Запуск: uv run python analysis/test_exit_point.py
Офлайн, 0 токенов.
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(КОРЕНЬ, "brain"))
import prefilter                                        # noqa: E402
import server                                           # noqa: E402

ошибки = []


def проверь(имя, условие):
    print(("  ✓ " if условие else "  ✗ ") + имя)
    if not условие:
        ошибки.append(имя)


print("тире не выходит наружу ни одним путём")
for исходный in ("Понял вас — подъеду сегодня",
                 "Работа — от 800",
                 "да, помогу. И номер оставьте — так проще договориться"):
    вых = server._contract({"reply": исходный, "model": "тест",
                            "state": {}, "missing": [], "ready": False, "done": False,
                            "usage": None})
    проверь("нет тире: %s" % исходный[:40], "—" not in вых["reply_text"] and "–" not in вых["reply_text"])
    проверь("и в reply тоже: %s" % исходный[:30], "—" not in (вых.get("reply") or ""))

print("пулы, которые дописываются ПОСЛЕ чистки, тоже чистые на выходе")
пулы = list(prefilter.PHONE_ASK_POOL) if hasattr(prefilter, "PHONE_ASK_POOL") else []
пулы += list(getattr(server, "_Q_ADDR", []))
пулы += list(getattr(server, "_ВОПРОС_МОДЕЛЬ_ТВ", []))
пулы += list(getattr(server, "_ВОПРОС_ПЛИТА", []))
грязные = [p for p in пулы if "—" in p or "–" in p]
print("     (в пулах тире есть в %d строках — их и снимает выход)" % len(грязные))
for p in грязные[:6]:
    вых = server._contract({"reply": "да, помогу.\n" + p, "model": "тест", "state": {},
                            "missing": [], "ready": False, "done": False, "usage": None})
    проверь("пул очищен: %s" % p[:40], "—" not in вых["reply_text"])

print("дефис внутри слова не трогаем")
вых = server._contract({"reply": "через час-полтора подъеду, дом 12к2", "model": "тест",
                        "state": {}, "missing": [], "ready": False, "done": False, "usage": None})
проверь("«час-полтора» цел", "час-полтора" in вых["reply_text"])

print()
if ошибки:
    print("ПРОВАЛЕНО: %d" % len(ошибки))
    for e in ошибки:
        print("  -", e)
    sys.exit(1)
print("Все проверки пройдены")
