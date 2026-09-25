# -*- coding: utf-8 -*-
"""КОМПЛЕКСНЫЙ прогон бота по РЕАЛЬНЫМ старым диалогам из разных бакетов.

Тянет разнотипные диалоги (удачные лиды / ценовые / согласование / обрыв на вопросе
оператора), балансирует по направлениям, ИСКЛЮЧАЕТ уже прогнанные (eval_*), гоняет
бота ход-за-ходом рядом с реальным оператором (compare_dialog через шлюз) и сохраняет
для судейского разбора всех дефектов (регламент/контекст/тон/доведение/озвучки/пустые).
"""
import sys, io, os, json, glob, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import server  # noqa

HERE = os.path.dirname(os.path.abspath(__file__))
BUCKETS = ["good_leads", "price", "soglasovanie", "ends_on_operator_q"]
PER_CAT = 8            # максимум на направление
TARGET = 24           # сколько всего взять

# уже прогнанные — не повторяем
seen = set()
for f in glob.glob(os.path.join(HERE, "eval_bot_vs_operator*.jsonl")):
    for line in open(f, encoding="utf-8"):
        try:
            seen.add(json.loads(line).get("key"))
        except Exception:
            pass

pool = {}
for b in BUCKETS:
    p = os.path.join(HERE, "mine_bucket_%s.jsonl" % b)
    if not os.path.exists(p):
        continue
    for line in open(p, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        k = d.get("key")
        if not k or k in seen or k in pool:
            continue
        m = d.get("m", [])
        n = len(m)
        ops = sum(1 for x in m if x.get("r") == "О")
        cls = sum(1 for x in m if x.get("r") == "К")
        # содержательные двусторонние диалоги
        if 4 <= n <= 30 and ops >= 2 and cls >= 2:
            d["_bucket"] = b
            pool[k] = d

# балансируем по направлениям, разнообразим по бакетам
picked, percat, perbucket = [], collections.Counter(), collections.Counter()
items = sorted(pool.values(), key=lambda d: (perbucket[d["_bucket"]], -len(d.get("m", []))))
# простой круговой отбор: идём по бакетам и категориям
by_cat = collections.defaultdict(list)
for d in pool.values():
    by_cat[d.get("cat", "unknown")].append(d)
for c in by_cat:
    by_cat[c].sort(key=lambda d: -len(d.get("m", [])))
order = ["mnc", "bt", "kp", "unknown"]
idx = {c: 0 for c in by_cat}
while len(picked) < TARGET:
    progressed = False
    for c in order:
        lst = by_cat.get(c, [])
        if idx.get(c, 0) < len(lst) and percat[c] < PER_CAT:
            picked.append(lst[idx[c]])
            percat[c] += 1
            idx[c] += 1
            progressed = True
            if len(picked) >= TARGET:
                break
    if not progressed:
        break

print("Отобрано %d диалогов | направления: %s | бакеты: %s" % (
    len(picked), dict(percat), dict(collections.Counter(d["_bucket"] for d in picked))))

out_path = os.path.join(HERE, "comprehensive_eval.jsonl")
fj = open(out_path, "w", encoding="utf-8")
for i, d in enumerate(picked, 1):
    msgs = [{"role": ("client" if x.get("r") == "К" else "operator"), "text": x.get("t", "")}
            for x in d.get("m", []) if x.get("t")]
    known = {"direction": d.get("cat"), "title": d.get("title")}
    try:
        pairs = server.compare_dialog(msgs, known=known, max_points=7)
    except Exception as e:
        print("  %s -> ошибка %r" % (d.get("key"), e))
        continue
    rec = {"key": d.get("key"), "cat": d.get("cat"), "bucket": d["_bucket"],
           "title": (d.get("title") or "")[:70], "pairs": pairs}
    fj.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("  [%d/%d] %s (%s/%s) — %d пар" % (i, len(picked), d.get("key"),
                                             d.get("cat"), d["_bucket"], len(pairs)))
fj.close()
print("Готово: comprehensive_eval.jsonl")
