# -*- coding: utf-8 -*-
"""ПЕРВОЕ ВПЕЧАТЛЕНИЕ на первичках: какие признаки ПЕРВОГО ответа оператора коррелируют
с (а) откликом клиента, (б) телефоном в диалоге. Плюс: скорость ответа vs конверсия,
слова-магниты и вопросы-глушители. 0 токенов."""
import paths  # noqa: E402  — единая точка правды по путям
import sys, io, os, re, glob, json, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import prefilter  # noqa

SRC = paths.MERGED
RE_PHONE = re.compile(r"(?:\+?7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}")
RE_URL = re.compile(r"https?://\S+")

feat_stats = collections.defaultdict(lambda: [0, 0, 0])   # feat -> [n, replied, phone]
lat_buckets = collections.defaultdict(lambda: [0, 0])     # bucket -> [n, phone]
word_stats = collections.defaultdict(lambda: [0, 0])      # word -> [n, phone]
killer_qs = collections.Counter()                         # вопрос 1-го ответа, клиент пропал
magnet_qs = collections.Counter()                         # вопрос 1-го ответа, дошло до телефона
total = 0

for fp in glob.glob(os.path.join(SRC, "*.json")):
    try:
        d = json.load(open(fp, encoding="utf-8"))
    except Exception:
        continue
    msgs = [m for m in (d.get("messages") or []) if (m.get("text") or "").strip()]
    if len(msgs) < 2 or msgs[0].get("role") != "client":
        continue
    t0 = msgs[0].get("text") or ""
    if RE_PHONE.search(t0) or prefilter.check_existing_client(t0, "", t0):
        continue
    # первый блок ответа оператора (все подряд operator-сообщения)
    i = 1
    while i < len(msgs) and msgs[i].get("role") == "client":
        i += 1
    if i >= len(msgs):
        continue
    j = i
    ops = []
    while j < len(msgs) and msgs[j].get("role") == "operator":
        ops.append(msgs[j])
        j += 1
    first = " \n".join((m.get("text") or "") for m in ops)
    fl = RE_URL.sub(" ", first).lower()
    replied = j < len(msgs)                                   # клиент написал что-то после
    blob = " ".join((m.get("text") or "") for m in msgs if m.get("role") == "client")
    phone = bool(RE_PHONE.search(blob))
    total += 1

    feats = {
        "вопрос в 1-м ответе": "?" in first,
        "цифра цены в 1-м ответе": bool(re.search(r"от\s*\d{3,}|\d{3,}\s*р", fl)),
        "увод «на месте/объём»": bool(re.search(r"на месте|по месту|объём|объем", fl)),
        "приём «помогу/да»": bool(re.search(r"помогу|да,|смогу", fl)),
        "экспертная догадка": bool(re.search(r"похоже|скорее всего|обычно это|может быть (?:насос|модуль|тэн)", fl)),
        "длинный (>25 слов)": len(fl.split()) > 25,
        "короткий (<=12 слов)": len(fl.split()) <= 12,
        "несколько пузырей": len(ops) > 1,
        "скобочка )": ")" in first and "(" not in first,
        "слот сразу (когда/удобно)": bool(re.search(r"когда|удобно|во сколько", fl)),
        "просьба данных сразу (адрес/номер)": bool(re.search(r"адрес|номер|телефон", fl)),
    }
    for k, v in feats.items():
        if v:
            feat_stats[k][0] += 1
            feat_stats[k][1] += replied
            feat_stats[k][2] += phone
    feat_stats["ВСЕ ПЕРВИЧКИ (база)"][0] += 1
    feat_stats["ВСЕ ПЕРВИЧКИ (база)"][1] += replied
    feat_stats["ВСЕ ПЕРВИЧКИ (база)"][2] += phone

    ts0, ts1 = msgs[0].get("ts"), ops[0].get("ts")
    if ts0 and ts1 and ts1 >= ts0:
        lat = ts1 - ts0
        b = ("<2мин" if lat < 120 else "2-10мин" if lat < 600 else
             "10-60мин" if lat < 3600 else ">1часа")
        lat_buckets[b][0] += 1
        lat_buckets[b][1] += phone

    for w in set(re.findall(r"[а-яё]{4,}", fl)):
        word_stats[w][0] += 1
        word_stats[w][1] += phone

    qs = re.findall(r"[^.!?\n]*\?", first)
    if qs:
        qn = re.sub(r"\s+", " ", qs[-1].strip().lower())[:70]
        if 8 <= len(qn):
            (magnet_qs if phone else (killer_qs if not replied else collections.Counter()))[qn] += 1

base_n, base_r, base_p = feat_stats["ВСЕ ПЕРВИЧКИ (база)"]
print("Первичек: %d | отклик после 1-го ответа: %.0f%% | телефон: %.1f%%\n"
      % (base_n, 100.0 * base_r / max(1, base_n), 100.0 * base_p / max(1, base_n)))
print("%-38s %6s %8s %8s" % ("ПРИЗНАК 1-го ОТВЕТА", "n", "отклик%", "телефон%"))
for k, (n, r, p) in sorted(feat_stats.items(), key=lambda x: -x[1][2] / max(1, x[1][0])):
    if n < 30:
        continue
    print("%-38s %6d %7.0f%% %7.1f%%" % (k, n, 100.0 * r / n, 100.0 * p / n))
print("\nСКОРОСТЬ 1-го ответа vs телефон:")
for b in ("<2мин", "2-10мин", "10-60мин", ">1часа"):
    n, p = lat_buckets[b]
    print("  %-9s n=%5d  телефон %.1f%%" % (b, n, 100.0 * p / max(1, n)))
print("\nСЛОВА-МАГНИТЫ (в 1-м ответе, lift к телефону, n>=60):")
scored = [(w, n, p, (p / n) / max(0.001, base_p / base_n)) for w, (n, p) in word_stats.items() if n >= 60]
for w, n, p, lift in sorted(scored, key=lambda x: -x[3])[:18]:
    print("  %-16s n=%4d  телефон %.1f%%  lift %.2f" % (w, n, 100.0 * p / n, lift))
print("\nСЛОВА-ГЛУШИТЕЛИ (lift < 0.75, n>=60):")
for w, n, p, lift in sorted(scored, key=lambda x: x[3])[:14]:
    print("  %-16s n=%4d  телефон %.1f%%  lift %.2f" % (w, n, 100.0 * p / n, lift))
print("\nВОПРОСЫ-ГЛУШИТЕЛИ (посл. вопрос 1-го ответа, клиент вообще не ответил; топ-12):")
for q, c in killer_qs.most_common(12):
    print("  %3d  %s" % (c, q))
print("\nВОПРОСЫ-МАГНИТЫ (дошло до телефона; топ-12):")
for q, c in magnet_qs.most_common(12):
    print("  %3d  %s" % (c, q))
