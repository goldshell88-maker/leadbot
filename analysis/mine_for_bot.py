# -*- coding: utf-8 -*-
"""Майнинг корпуса Jivo под улучшение ЛИД-БОТА (без обращения к модели, 0 токенов).

Идёт по всем диалогам, раскладывает по «корзинам провалов/ситуаций» и выгружает:
  - агрегаты (analysis/mine_stats.json + печать)
  - все ВОПРОСЫ клиентов (что бот обязан уметь) -> mine_faq.txt
  - топ первых сообщений клиента -> внутри mine_stats
  - обезличенные выборки по корзинам -> mine_bucket_*.jsonl (их читает Claude)

Запуск:  py analysis\\mine_for_bot.py
Только stdlib.
"""
import paths  # noqa: E402  — единая точка правды по путям
import json, os, re, glob, io, sys, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SRC = paths.DIALOGS
OUT = paths.ANALYSIS
os.makedirs(OUT, exist_ok=True)

RE_PHONE = re.compile(r"(?:\+?7|8)?[\s\-\(\)]*\d{3}[\s\-\(\)]*\d{3}[\s\-\(\)]*\d{2}[\s\-\(\)]*\d{2}")
RE_EMAIL = re.compile(r"[\w\.\-]+@[\w\.\-]+\.\w+")
RE_URL   = re.compile(r"https?://\S+")
RE_IMG   = re.compile(r"🖼\s*\S+")
RE_PRICE = re.compile(r"\d[\d\s]{1,7}\s*(?:р\b|р\.|руб|₽|тыс|т\.р)", re.I)

CAT_KEYS = {
    "mnc": ["мебел", "кухн", "сборк", "сантех", "электрик", "муж на час", "ремонт кварт",
            "плинтус", "карниз", "полк", "навес", "столешн", "двер", "замок", "замк"],
    "bt":  ["стиральн", "холодильн", "посудомоеч", "плита", "духов", "варочн", "телевизор",
            "бытов", "микроволнов", "кондицион", "водонагрев", "морозильн", "пылесос", "кофемаш"],
    "kp":  ["компьютер", "ноутбук", "пк ", "windows", "виндовс", "интернет", "роутер",
            "программ", "вирус", "переустанов", "видеокарт", "материнск", "монитор", "приставк"],
}

def redact(t):
    if not t: return ""
    t = RE_IMG.sub("<ФОТО>", t)
    t = RE_URL.sub("<ССЫЛКА>", t)
    t = RE_EMAIL.sub("<EMAIL>", t)
    t = RE_PHONE.sub("<ТЕЛ>", t)
    return t

def guess_cat(meta):
    blob = " ".join([str(meta.get("page", {}).get("title", "")),
                     str(meta.get("page", {}).get("url", ""))]).lower()
    for cat, keys in CAT_KEYS.items():
        if any(k in blob for k in keys):
            return cat
    return "unknown"

def is_avito(meta):
    url = (meta.get("page", {}) or {}).get("url", "") or ""
    v = meta.get("visitor", {}) or {}
    socials = ((v.get("social") or {}).get("socialProfiles") or [])
    if socials and socials[0].get("typeName") == "av":
        return True
    return "avito" in url

# ---- детекторы ситуаций (по тексту клиента) ----
RX_MSGR   = re.compile(r"ватсап|вотсап|вацап|whats\s*app|whatsapp|телеграм|телега|\bтг\b|viber|вайбер|"
                       r"напиш(?:и|ите).{0,12}(?:вотс|ватс|телег|вайб)", re.I)
RX_CALLME = re.compile(r"позвон(?:и|ите|ит)|перезвон|наберите|наберёте|набер[её]т|звоните|"
                       r"свяжитесь|можете позвонить|можно позвонить", re.I)
RX_AVITO_CALL = re.compile(r"(?:звоните|пишите|номер).{0,20}авито|через авито|с авито|по авито|тут в чате|"
                           r"здесь в чате|в этом чате", re.I)
RX_PRICE_Q = re.compile(r"скольк|\bцен|стоимост|\bстоит\b|поч[её]м|прайс|ценник|расцен|за скольк|"
                        r"во сколько обойд|сколько будет|сколько выйдет|сколько возьм", re.I)
RX_QMARK  = re.compile(r"[^.!?\n]*\?")
RX_SOGLAS = re.compile(r"кондицион|сплит[\s-]?систем|гипсокартон|отдел(?:ка|ку|очн)|штукатур|"
                       r"поклей|обои|ламинат|стяжк|натяжн|потолок|плитк|санузел под ключ|"
                       r"ремонт под ключ|промышленн", re.I)
RX_URGENT = re.compile(r"срочно|сегодня|сейчас|как можно быстрее|побыстрее|прямо сейчас|"
                       r"в течение часа|асап|\bжду\b", re.I)
RX_WARRANTY = re.compile(r"гаранти|если не почин|если не помож|вдруг слома|повторн", re.I)

def norm_q(t):
    t = redact(t).lower().strip()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[«»\"'`]", "", t)
    return t.strip(" ?!.,-—")

files = sorted(glob.glob(os.path.join(SRC, "*.json")))
S = collections.Counter()
by_cat = collections.Counter()
cat_phone = collections.Counter()      # телефон собран по категории
cat_total_two = collections.Counter()  # двусторонних по категории
first_msgs = collections.Counter()
questions = collections.Counter()
questions_raw = []
latencies = []                          # сек до первого ответа оператора (двусторонние)
buckets = collections.defaultdict(list)

def add_bucket(name, d, meta, msgs, tag=""):
    if len(buckets[name]) >= 60:
        return
    red = [{"r": ("К" if m.get("role") == "client" else "О"), "t": redact(m.get("text", ""))}
           for m in msgs if m.get("text")]
    buckets[name].append({
        "key": d.get("conversation_key"), "cat": guess_cat(meta),
        "title": (meta.get("page", {}) or {}).get("title", ""),
        "n": len(msgs), "tag": tag, "m": red,
    })

for fp in files:
    try:
        d = json.load(open(fp, encoding="utf-8"))
    except Exception:
        S["broken"] += 1
        continue
    msgs = d.get("messages", []) or []
    meta = d.get("meta", {}) or {}
    S["total"] += 1
    if not is_avito(meta):
        S["not_avito"] += 1
    cat = guess_cat(meta)
    by_cat[cat] += 1

    client_msgs = [m for m in msgs if m.get("role") == "client"]
    op_msgs     = [m for m in msgs if m.get("role") == "operator"]
    n_client, n_op = len(client_msgs), len(op_msgs)
    client_text = "\n".join(m.get("text", "") for m in client_msgs)
    op_text     = "\n".join(m.get("text", "") for m in op_msgs)
    two_sided = n_client > 0 and n_op > 0

    # --- воронка ---
    if n_client == 0:
        S["no_client"] += 1
    if n_op == 0 and n_client > 0:
        S["never_answered"] += 1          # клиент написал — оператор молчит
        add_bucket("never_answered", d, meta, msgs)
    if two_sided:
        S["two_sided"] += 1
        cat_total_two[cat] += 1

    visitor_phone = ((meta.get("visitor") or {}).get("phone"))
    phone_ok = bool(RE_PHONE.search(client_text) or visitor_phone)
    if phone_ok:
        S["phone_collected"] += 1
        if two_sided: cat_phone[cat] += 1
    elif two_sided:
        S["two_sided_no_phone"] += 1

    # --- первое сообщение клиента ---
    if client_msgs:
        fm = norm_q(client_msgs[0].get("text", ""))
        if fm: first_msgs[fm[:80]] += 1

    # --- вопросы клиента (готовый FAQ для бота) ---
    for m in client_msgs:
        for q in RX_QMARK.findall(m.get("text", "") or ""):
            qn = norm_q(q)
            if 6 <= len(qn) <= 120:
                questions[qn] += 1
                if len(questions_raw) < 4000:
                    questions_raw.append(qn)

    # --- ситуации по тексту клиента ---
    if RX_MSGR.search(client_text):
        S["wants_messenger"] += 1
        add_bucket("messenger", d, meta, msgs)
    if RX_AVITO_CALL.search(client_text):
        S["says_call_via_avito"] += 1
        add_bucket("avito_call", d, meta, msgs)
    if RX_PRICE_Q.search(client_text):
        S["price_question"] += 1
        if RE_PRICE.search(op_text):
            S["price_q_operator_named"] += 1
        add_bucket("price", d, meta, msgs,
                   tag=("оператор назвал цифру" if RE_PRICE.search(op_text) else "цифру не назвал"))
    if RX_SOGLAS.search(client_text):
        S["soglasovanie_topic"] += 1
        add_bucket("soglasovanie", d, meta, msgs)
    if RX_URGENT.search(client_text):
        S["urgent"] += 1
    if RX_WARRANTY.search(client_text):
        S["warranty_q"] += 1
        add_bucket("warranty", d, meta, msgs)

    # --- концовки: где терялся лид ---
    if msgs:
        last = msgs[-1]
        last_txt = (last.get("text", "") or "").strip()
        if last.get("role") == "client":
            S["ends_on_client"] += 1       # клиент написал последним, ответа нет
            if two_sided:
                add_bucket("ends_on_client", d, meta, msgs)
        elif last.get("role") == "operator":
            if last_txt.endswith("?"):
                S["ends_on_operator_q"] += 1   # оператор задал вопрос — клиент пропал
                add_bucket("ends_on_operator_q", d, meta, msgs)

    # --- латентность первого ответа оператора ---
    if two_sided:
        t0 = client_msgs[0].get("ts")
        t_op = next((m.get("ts") for m in msgs if m.get("role") == "operator"), None)
        if t0 and t_op and t_op >= t0:
            latencies.append(t_op - t0)

    # --- эталоны: собрали телефон, диалог живой ---
    if two_sided and phone_ok and 4 <= len(msgs) <= 40:
        add_bucket("good_leads", d, meta, msgs)

    # --- отказные темы: сверка с pre-фильтром ---
    # (импорт локальный, чтобы скрипт работал даже без prefilter)

# pre-фильтр отдельно (может отсутствовать)
try:
    sys.path.insert(0, os.path.dirname(OUT) + r"\brain")
    sys.path.insert(0, os.path.join(os.path.dirname(OUT), "brain"))
    import prefilter  # noqa
    refuse_hits = collections.Counter()
    for fp in files:
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        msgs = d.get("messages", []) or []
        ct = "\n".join(m.get("text", "") for m in msgs if m.get("role") == "client")
        r = prefilter.check_refuse(ct)
        if r:
            refuse_hits[r["kind"]] += 1
    S_refuse = dict(refuse_hits)
except Exception as e:
    S_refuse = {"(prefilter недоступен)": str(e)}

# ---- вывод ----
def pct(n, base=None):
    b = base if base is not None else (S["total"] or 1)
    return "%d (%.0f%%)" % (n, 100.0 * n / b)

print("=" * 64)
print("МАЙНИНГ КОРПУСА JIVO ПОД ЛИД-БОТА")
print("=" * 64)
print("Всего диалогов:", S["total"], "| битых:", S["broken"], "| не-avito:", S["not_avito"])
print("Двусторонних (клиент+оператор):", pct(S["two_sided"]))
print("Клиент написал — оператор НЕ ОТВЕТИЛ вообще:", pct(S["never_answered"]))
print()
print("ТЕЛЕФОН собран (весь корпус):", pct(S["phone_collected"]))
print("  из двусторонних БЕЗ телефона:", pct(S["two_sided_no_phone"], S["two_sided"] or 1))
print("Ценовой вопрос от клиента:", pct(S["price_question"]),
      "| из них оператор назвал цифру:", pct(S["price_q_operator_named"], S["price_question"] or 1))
print("Просит увести в МЕССЕНДЖЕР (ватсап/тг/вайбер):", pct(S["wants_messenger"]))
print("Говорит «звоните/пишите с авито»:", pct(S["says_call_via_avito"]))
print("Тема «по согласованию» (кондей/отделка/потолки…):", pct(S["soglasovanie_topic"]))
print("Срочность:", pct(S["urgent"]), "| Вопрос про гарантию:", pct(S["warranty_q"]))
print()
print("Оборвалось на реплике КЛИЕНТА (ответа нет):", pct(S["ends_on_client"]))
print("Оборвалось на ВОПРОСЕ оператора (клиент пропал):", pct(S["ends_on_operator_q"]))
if latencies:
    latencies.sort()
    med = latencies[len(latencies)//2]
    over10 = sum(1 for x in latencies if x > 600)
    over60 = sum(1 for x in latencies if x > 3600)
    print("Ответ оператора: медиана %d мин | >10 мин: %s | >1 ч: %s" % (
        med // 60, pct(over10, len(latencies)), pct(over60, len(latencies))))
print()
print("Направления:", dict(by_cat.most_common()))
print("Телефон по направлению (собран/двусторонних):",
      {c: "%d/%d" % (cat_phone[c], cat_total_two[c]) for c in cat_total_two})
print("Отказные темы (pre-фильтр по клиенту):", S_refuse)
print()
print("ТОП-25 первых сообщений клиента:")
for t, c in first_msgs.most_common(25):
    print("  %3d  %s" % (c, t))
print()
print("ТОП-40 вопросов клиента (нормализованные):")
for q, c in questions.most_common(40):
    print("  %3d  %s" % (c, q))

# ---- файлы ----
json.dump({
    "stats": dict(S), "by_cat": dict(by_cat),
    "cat_phone": dict(cat_phone), "cat_total_two": dict(cat_total_two),
    "refuse": S_refuse,
    "first_msgs_top": first_msgs.most_common(60),
    "questions_top": questions.most_common(120),
}, open(os.path.join(OUT, "mine_stats.json"), "w", encoding="utf-8"),
   ensure_ascii=False, indent=2)

with open(os.path.join(OUT, "mine_faq.txt"), "w", encoding="utf-8") as f:
    f.write("ВОПРОСЫ КЛИЕНТОВ (нормализованные, по частоте) — что бот обязан уметь\n")
    f.write("=" * 60 + "\n")
    for q, c in questions.most_common(300):
        f.write("%3d  %s\n" % (c, q))

for name, items in buckets.items():
    items.sort(key=lambda x: str(x["key"]))
    with open(os.path.join(OUT, "mine_bucket_%s.jsonl" % name), "w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

print()
print("Файлы: mine_stats.json, mine_faq.txt, mine_bucket_*.jsonl")
print("Корзины:", {k: len(v) for k, v in buckets.items()})
