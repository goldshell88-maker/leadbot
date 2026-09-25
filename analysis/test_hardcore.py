# -*- coding: utf-8 -*-
"""ХАРДКОР-ТЕСТ: реальные эталонные диалоги операторов + жёсткие стресс-сценарии.
Детерминированные проверки ключевых правил заказчика:
- МНЧ: НЕ называть «от N» в лоб на первый вопрос о цене (увод на осмотр).
- Клиент просит позвонить → передать оператору (handoff/call_needed).
- Давление по цене: держать, без вилки; 1000 только на вопрос про ВЫЕЗД.
- Наличие детали не обещать. Матрица/полосы → отказ. КП → 1000 сразу.
"""
import sys, io, os, re, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import server  # noqa

HERE = os.path.dirname(os.path.abspath(__file__))
PRICE_NUM = re.compile(r"от\s*\d{3,}|\d{3,4}\s*(?:р\b|руб|₽)|\b(?:800|1000|1500|2000|3000|5000)\b")
VILKA = re.compile(r"\d{3,4}\s*[-–—]\s*\d{3,4}|от\s*\d+\s*до\s*\d+")


def run(turns, known):
    hist, rows = [], []
    for t in turns:
        hist.append({"role": "client", "text": t})
        res = server.run_relay(hist, known=known)
        rows.append({"c": t, "b": (res.get("reply") or "").strip(), "res": res})
        hist.append({"role": "operator", "text": res.get("reply") or ""})
    return rows


CASES = []


def case(name, turns, known, checker):
    CASES.append((name, turns, known, checker))


# 1. МНЧ сборка шкафа — НЕ давать цену в лоб (ошибка заказчика)
case("МНЧ шкаф: не цену в лоб", ["Доброе утро, сколько будет стоить собрать трёхстворчатый шкаф?"],
     {"direction": "mnc"},
     lambda r: (not PRICE_NUM.search(r[0]["b"].lower()), "первый ответ БЕЗ числа (увод на осмотр): «%s»" % r[0]["b"]))

# 2. Клиент просит позвонить → оператору
case("Звонок → оператору", ["установлю windows, куда подъехать?", "89001112251 можете мне позвонить обговорим детали"],
     {"direction": "kp"},
     lambda r: (bool(r[-1]["res"].get("handoff") or r[-1]["res"].get("call_needed")),
                "handoff=%s call_needed=%s reply=«%s»" % (r[-1]["res"].get("handoff"), r[-1]["res"].get("call_needed"), r[-1]["b"])))

# 3. Давление по цене (как в эталоне, ТВ) — держать, без вилки, 1000 на «за приезд»
case("Давление по цене ТВ", ["Глючит LG, включается сам пока с розетки не вытащить",
                             "по деньгам что?", "вы мастер, от скольки до скольки по деньгам",
                             "за приезд сколько?"],
     {"direction": "bt"},
     lambda r: (
         (not VILKA.search(" ".join(x["b"] for x in r))) and ("1000" in r[-1]["b"]),
         "нет вилки=%s; на «за приезд» 1000=%s (%s)" % (
             not VILKA.search(" ".join(x["b"] for x in r)), "1000" in r[-1]["b"], r[-1]["b"])))

# 4. Наличие детали не обещать (как в эталонном диалоге)
case("Деталь в наличии не обещать", ["ТВ 2006, темнота, инвертор не работает, можно найти деталь и сделать?",
                                     "у вас эта деталь есть в наличии?"],
     {"direction": "bt"},
     lambda r: (not re.search(r"да,?\s*(?:есть|в наличии)|деталь\s*есть|есть\s*в наличии", r[-1]["b"].lower()),
                "не обещал наличие: «%s»" % r[-1]["b"]))

# 5. МНЧ лестница ДВУХ касаний (политика 29.07 по корпусу): этап1 увод БЕЗ числа,
# этап2 («а точнее примерно?») — уже «от N»; третьего увода у живых не бывает
case("МНЧ лестница цены", ["поменять смеситель на кухне сколько будет?", "а точнее хоть примерно?",
                           "ну хоть от какой суммы, дорого же наверное"],
     {"direction": "mnc"},
     lambda r: (not PRICE_NUM.search(r[0]["b"].lower()) and bool(PRICE_NUM.search(r[1]["b"].lower())),
                "этап1 без числа=%s, этап2 с «от N»=%s | ходы: %s" % (
                    not PRICE_NUM.search(r[0]["b"].lower()), bool(PRICE_NUM.search(r[1]["b"].lower())),
                    " || ".join(x["b"] for x in r))))

# 6. Матрица/полосы ТВ → отказ
case("Полосы ТВ → отказ", ["телевизор показывает полосы по всему экрану, поможете?"],
     {"direction": "bt"},
     lambda r: (bool(r[0]["res"].get("deflect") or r[0]["res"].get("handoff")) or "не по моей части" in r[0]["b"].lower(),
                "отказ/handoff: «%s»" % r[0]["b"]))

# 7. КП Windows → 1000 сразу (КП даёт число)
case("КП Windows → 1000 сразу", ["Здравствуйте, сколько установка windows 11?"],
     {"direction": "kp"},
     lambda r: ("1000" in r[0]["b"], "есть 1000 в первом ответе: «%s»" % r[0]["b"]))

# 8. Розетки — приём и движение к записи, оплата «как удобно»
case("Розетки: оплата как удобно", ["розетки чините?", "не работают 2 розетки",
                                     "адрес Полевая 7 кв 3, 89001112252, этаж 1 подъезд 1", "переводом или картой можно?"],
     {"direction": "mnc"},
     lambda r: (not re.search(r"вперёд|предоплат|не\s*принима", r[-1]["b"].lower()),
                "оплата не отвергнута: «%s»" % r[-1]["b"]))


def main():
    out, npass = [], 0
    dash_hits = []
    print("=== ХАРДКОР: %d сценариев ===\n" % len(CASES))
    for name, turns, known, checker in CASES:
        rows = run(turns, known)
        for x in rows:                      # мастера тире не пишут, нигде
            if "—" in x["b"] or "–" in x["b"]:
                dash_hits.append((name, x["b"][:60]))
        ok, note = checker(rows)
        npass += 1 if ok else 0
        print("%s %s" % ("✓" if ok else "✗ FAIL", name))
        print("   → %s" % note)
        for x in rows:
            print("   К: %s" % x["c"][:66])
            print("   Б: %s" % (x["b"][:76] if x["b"] else "(пусто/оператору)"))
        print()
        out.append({"name": name, "ok": ok, "note": note, "rows": [{"c": x["c"], "b": x["b"]} for x in rows]})
    with open(os.path.join(HERE, "test_hardcore.jsonl"), "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    print("=" * 55)
    print("Тире в ответах: %d %s" % (len(dash_hits), "(НЕДОПУСТИМО!)" if dash_hits else "(чисто)"))
    for n, b in dash_hits[:6]:
        print("   [%s] %s" % (n, b))
    print("ИТОГО: %d/%d прошло%s" % (npass, len(CASES), "" if not dash_hits else " + ТИРЕ!"))


if __name__ == "__main__":
    main()
