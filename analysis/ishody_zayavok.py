# -*- coding: utf-8 -*-
"""ЗАМКНУТЫЙ ЦИКЛ КАЧЕСТВА: исход заявки в CRM ← приёмы бота в диалоге (30.08).

Зачем. Пулы реплик и пороги передач сейчас отбираются по замерам корпуса ЖИВЫХ
операторов — это прошлое. Расширение уже опрашивает CRM (будильник раз в 30 мин)
и приносит статус каждой созданной заявки: «Готов» (выезд состоялся), «Отказ»,
«Отмена Филиала», «Отмена КЦ», «Не оформлена». Связав статус с диалогом, получаем
прямую метрику: какой ПРИЁМ бота ведёт к выезду, а какой — к отмене.

Как связывается. Ключ очереди CRM = телефон:дата; в теневом журнале боевых ходов
(data/shadow.jsonl) телефон лежит в собранной заявке. Приёмы считаются по репликам
бота в диалоге: «запишу вас?» (сильнейший приём по прежнему замеру, +35,9 п.п.),
конкретное окно приезда, озвучка цены, просьба номера.

Запуск: uv run python analysis/ishody_zayavok.py           # локально (данные с сервера)
        (файлы crm_queue.jsonl и shadow.jsonl скачать с leadchat-watch заранее)
Офлайн, 0 токенов. Телефоны в вывод не печатаются.
"""
import io
import json
import os
import re
import sys
import collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ОЧЕРЕДЬ = os.path.join(КОРЕНЬ, "data", "crm_queue.jsonl")
ТЕНЬ = os.path.join(КОРЕНЬ, "data", "shadow.jsonl")

ПРИЁМЫ = {
    "запишу вас?": re.compile(r"запишу\s+вас|записать\s+вас\??", re.I),
    "конкретное окно": re.compile(r"\d{1,2}[:.]\d{2}\s*[-–—]\s*\d{1,2}[:.]\d{2}|к\s*\d{1,2}[:.]\d{2}", re.I),
    "час-полтора": re.compile(r"час\w*[\s,-]*полтор|в течение часа", re.I),
    "озвучил цену": re.compile(r"от\s*\d{3,}|\d{3,}\s*(?:р\b|руб)", re.I),
    "просил номер": re.compile(r"номер|телефон", re.I),
}

# ── заявки с известным исходом ───────────────────────────────────────────────
заявки = {}
try:
    for ln in io.open(ОЧЕРЕДЬ, encoding="utf-8"):
        try:
            d = json.loads(ln)
        except Exception:
            continue
        к = d.get("key") or ""
        if not к:
            continue
        з = заявки.setdefault(к, {})
        з.update({x: d[x] for x in ("status", "id", "crm_статус", "vid") if x in d})
except FileNotFoundError:
    print("нет data/crm_queue.jsonl — скачайте с сервера: "
          "ssh leadchat-watch cat /opt/leadbot/data/crm_queue.jsonl > data/crm_queue.jsonl")
    sys.exit(2)

с_исходом = {к: з for к, з in заявки.items() if з.get("crm_статус")}
print("заявок в очереди: %d | создано в CRM: %d | С ИЗВЕСТНЫМ ИСХОДОМ: %d"
      % (len(заявки), sum(1 for з in заявки.values() if з.get("id")), len(с_исходом)))

if len(с_исходом) < 10:
    print("\nисходов меньше десяти — выводы делать рано. Будильник расширения приносит")
    print("статусы раз в 30 минут С ОФИСНОГО ПК; если он выключен, данных не будет.")
    print("Инструмент готов: перезапустите замер, когда наберётся 30+ исходов.")
    sys.exit(0)

# ── диалоги по тем же ключам ────────────────────────────────────────────────
диалоги = collections.defaultdict(list)
try:
    for ln in io.open(ТЕНЬ, encoding="utf-8"):
        try:
            d = json.loads(ln)
        except Exception:
            continue
        тел = re.sub(r"\D", "", str((d.get("заявка") or {}).get("phone") or ""))[-10:]
        if тел:
            диалоги[тел].append(str(d.get("бот") or ""))
except FileNotFoundError:
    print("нет data/shadow.jsonl — приёмы бота посчитать не по чему")
    sys.exit(2)

# ── исход × приём ────────────────────────────────────────────────────────────
итог = collections.defaultdict(lambda: collections.Counter())
for к, з in с_исходом.items():
    тел = к.split(":")[0][-10:]
    реплики = " ".join(диалоги.get(тел) or [])
    исход = з["crm_статус"]
    итог["ВСЕГО"][исход] += 1
    for имя, rx in ПРИЁМЫ.items():
        if реплики and rx.search(реплики):
            итог[имя][исход] += 1

print("\n%-18s %s" % ("приём \\ исход", "  ".join("%-14s" % s for s in
      ("Готов", "Отказ", "Отмена Филиала", "Отмена КЦ", "Не оформлена"))))
for имя, счёт in итог.items():
    всего = sum(счёт.values())
    print("%-18s %s | всего %d, «Готов» %.0f%%" % (
        имя[:18], "  ".join("%-14d" % счёт.get(s, 0) for s in
        ("Готов", "Отказ", "Отмена Филиала", "Отмена КЦ", "Не оформлена")),
        всего, 100.0 * счёт.get("Готов", 0) / max(всего, 1)))
print("\nЧитать так: приём с долей «Готов» ниже строки ВСЕГО — кандидат на замену.")
