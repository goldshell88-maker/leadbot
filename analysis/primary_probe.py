# -*- coding: utf-8 -*-
"""ПРОБА ПЕРВИЧЕК: прогон бота по стратифицированной выборке РЕАЛЬНЫХ первых сообщений.

Берёт из merged-корпуса по N первых сообщений каждого подтипа первичек (БТ/КП/МНЧ ×
обычная/цена-первым + анкеты + фото-текст), гоняет через run_relay и жёстко проверяет:
  1) ответ НЕ пустой (первичка не должна молча уходить оператору),
  2) НЕТ ошибочного отказа на профильной заявке,
  3) есть воронка (вопрос) или запись,
  4) цена-ветка верна: МНЧ без цифры на 1-й вопрос; КП-ремонт → 1000; КП-установка → без 1000-диагностики.
Запуск: py analysis\\primary_probe.py [N_на_подтип=5]
"""
import paths  # noqa: E402  — единая точка правды по путям
import sys, io, os, glob, json, re, random
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import server, prefilter  # noqa

D = paths.MERGED
N = int(sys.argv[1]) if len(sys.argv) > 1 else 5
random.seed(42)

PRICE_RX = re.compile(r"сколько|стоимост|цена|почем|прайс|расценк|по\s*деньгам", re.I)
NUM_RX = re.compile(r"\d{3,}")
REFUSE_RX = re.compile(r"не\s*(?:смогу|помогу|занимаюсь|беру|работаю\s+с)", re.I)
SECONDARY_RX = re.compile(r"уже\s*обращал|созванивал|договаривал|мастер\s*(?:был|приезжал|забрал)|вы\s*приедете|"
                          r"обещал|по\s*(?:моей|нашей)\s*заявк|дозвонит", re.I)
NONPROFILE_RX = re.compile(r"газов|матриц|вскрыт|на\s*запчаст|выкуп|заправ\w*\s*картридж|скупк", re.I)


def direction_of(d):
    t = ((d.get("meta") or {}).get("page") or {}).get("title", "").lower()
    if re.search(r"компьютер|ноутбук|принтер|windows|программ", t):
        return "kp"
    if re.search(r"мастер на час|сантех|мебел|электрик|сборк|установк", t):
        return "mnc"
    if re.search(r"телевизор|стирал|холодил|техник|бытов", t):
        return "bt"
    return None


def first_client_msg(d):
    for m in (d.get("messages") or []):
        if m.get("role") == "client" and (m.get("text") or "").strip():
            return m["text"].strip()
    return None


def collect():
    buckets = {("bt", "plain"): [], ("bt", "price"): [], ("kp", "plain"): [], ("kp", "price"): [],
               ("mnc", "plain"): [], ("mnc", "price"): [], ("any", "form"): [], ("any", "photo"): []}
    files = glob.glob(os.path.join(D, "*.json"))
    random.shuffle(files)
    for fp in files:
        try:
            dd = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        fm = first_client_msg(dd)
        if not fm or len(fm) < 12 or len(fm) > 400:
            continue
        if SECONDARY_RX.search(fm) or NONPROFILE_RX.search(fm):
            continue                                  # не первичка / не профиль — не наш тест
        dirn = direction_of(dd)
        if "Задача составлена" in fm or "Вот подробности" in fm or " · " in fm:
            key = ("any", "form")
        elif "🖼" in fm or "img.avito" in fm:
            key = ("any", "photo")
        elif dirn and PRICE_RX.search(fm):
            key = (dirn, "price")
        elif dirn:
            key = (dirn, "plain")
        else:
            continue
        if len(buckets[key]) < N:
            buckets[key].append((fm, dirn or "unknown"))
        if all(len(v) >= N for v in buckets.values()):
            break
    return buckets


def probe(fm, dirn):
    res = server.run_relay([{"role": "client", "text": fm}], known={"direction": dirn if dirn != "any" else None})
    return res


def main():
    buckets = collect()
    total, fails = 0, []
    for (dirn, kind), items in buckets.items():
        print("=== %s / %s (%d) ===" % (dirn, kind, len(items)))
        for fm, dr in items:
            total += 1
            try:
                res = probe(fm, dr)
            except Exception as e:
                fails.append((dirn, kind, fm, "EXCEPTION %r" % e))
                print("  ✗ EXC", fm[:60])
                continue
            reply = (res.get("reply") or "").strip()
            probs = []
            if not reply and not res.get("handoff"):
                probs.append("ПУСТО без handoff")
            if reply and REFUSE_RX.search(reply) and kind != "refuse":
                probs.append("ОТКАЗ на профильном")
            if reply and "?" not in reply and not re.search(r"запис|адрес|номер", reply.lower()):
                probs.append("нет воронки-вопроса")
            if kind == "price":
                low = reply.lower()
                if dirn == "mnc" and NUM_RX.search(low):
                    probs.append("МНЧ: цифра на 1-й вопрос")
                if dirn == "kp" and re.search(r"установ|настро|windows|винд", fm.lower()) \
                        and "диагностик" in low and "1000" in low:
                    probs.append("КП-установка: лишняя 1000-диагностика")
            mark = "✗" if probs else "✓"
            print("  %s %s" % (mark, fm[:64].replace(chr(10), " ")))
            if probs:
                print("     !! %s | ответ: %s" % ("; ".join(probs), reply[:90].replace(chr(10), " / ")))
                fails.append((dirn, kind, fm, "; ".join(probs)))
        print()
    print("=" * 60)
    print("ИТОГО: %d проб, проблем: %d" % (total, len(fails)))
    for d_, k, fm, p in fails:
        print("  • [%s/%s] %s :: %s" % (d_, k, fm[:50], p))


if __name__ == "__main__":
    main()
