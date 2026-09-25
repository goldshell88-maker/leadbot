# -*- coding: utf-8 -*-
"""Прогон нашего бота по КАЧЕСТВЕННЫМ реальным диалогам и сравнение с оператором.

Берёт эталоны (оператор довёл до записи) из mine_bucket_good_leads.jsonl, отбирает
самые содержательные и разнообразные по направлениям, и на КАЖДОМ ходе клиента
получает ответ нашего бота (server.compare_dialog → suggest_reply через шлюз) рядом
с реальным ответом оператора. Сохраняет для судейского разбора.

Запуск:  py analysis\\eval_bot_vs_operator.py [N]
"""
import sys, io, json, os, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import server  # noqa

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "mine_bucket_good_leads.jsonl")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 12
PER_CAT = 5

dialogs = []
with open(SRC, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            dialogs.append(json.loads(line))


def score(d):
    m = d.get("m", [])
    blob = " ".join(x.get("t", "") for x in m).lower()
    ops = [x for x in m if x.get("r") == "О"]
    s = 0
    if 6 <= d.get("n", 0) <= 30:
        s += 2
    if any(k in blob for k in ["цен", "стоит", "сколько", "руб", "от 8", "от 1", "от 5", "почём", "почем"]):
        s += 2                      # есть ценовой момент — самое интересное для сверки
    if len(ops) >= 3:
        s += 1
    if d.get("cat") in ("bt", "kp", "mnc"):
        s += 1
    # признаки хорошей отработки: запись/адрес/номер
    if any(k in blob for k in ["записал", "записываю", "номер", "адрес", "подъед", "подъех"]):
        s += 1
    return s


SUF = sys.argv[2] if len(sys.argv) > 2 else ""
EXCLUDED = set()
try:                                    # не повторять диалоги из базового прогона (пачка 1)
    with open(os.path.join(HERE, "eval_bot_vs_operator.jsonl"), encoding="utf-8") as f:
        for line in f:
            try:
                EXCLUDED.add(json.loads(line).get("key"))
            except Exception:
                pass
except FileNotFoundError:
    pass

dialogs.sort(key=score, reverse=True)
picked, percat = [], collections.Counter()
for d in dialogs:
    if SUF and d.get("key") in EXCLUDED:
        continue
    c = d.get("cat")
    if percat[c] >= PER_CAT:
        continue
    picked.append(d)
    percat[c] += 1
    if len(picked) >= N:
        break

print("Отобрано %d диалогов, по направлениям: %s (исключено %d из пачки 1)" % (
    len(picked), dict(percat), len(EXCLUDED) if SUF else 0))

out_jsonl = os.path.join(HERE, "eval_bot_vs_operator%s.jsonl" % SUF)
out_md = os.path.join(HERE, "eval_bot_vs_operator%s.md" % SUF)
fj = open(out_jsonl, "w", encoding="utf-8")
fm = open(out_md, "w", encoding="utf-8")
fm.write("# Бот vs оператор на качественных диалогах\n\n")
CATRU = {"bt": "БТ", "kp": "КП", "mnc": "МНЧ", "unknown": "—"}

for idx, d in enumerate(picked, 1):
    msgs = [{"role": ("client" if x.get("r") == "К" else "operator"), "text": x.get("t", "")}
            for x in d.get("m", []) if x.get("t")]
    known = {"direction": d.get("cat"), "title": d.get("title")}
    try:
        pairs = server.compare_dialog(msgs, known=known, max_points=6)
    except Exception as e:
        print("  диалог %s -> ошибка: %r" % (d.get("key"), e))
        continue
    rec = {"key": d.get("key"), "cat": d.get("cat"), "title": d.get("title"), "pairs": pairs}
    fj.write(json.dumps(rec, ensure_ascii=False) + "\n")
    fm.write("## %d. %s · %s\n\n" % (idx, CATRU.get(d.get("cat"), d.get("cat")), (d.get("title") or "")[:70]))
    for p in pairs:
        fm.write("**Клиент:** %s\n\n" % (p.get("client", "").replace("\n", " / ")))
        fm.write("- **Оператор:** %s\n" % (p.get("operator", "").replace("\n", " / ") or "—"))
        fm.write("- **Бот:** %s\n\n" % (p.get("bot", "").replace("\n", " / ") or "—"))
    fm.write("---\n\n")
    print("  [%d/%d] %s (%s) — %d пар" % (idx, len(picked), d.get("key"), d.get("cat"), len(pairs)))

fj.close()
fm.close()
print("Готово: eval_bot_vs_operator.md / .jsonl")
