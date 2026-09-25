# -*- coding: utf-8 -*-
"""Структурный разбор всех диалогов Jivo (без обращения к модели).

Читает <приёмник Jivo>/data/dialogs/*.json (путь берётся из brain/paths.py),
считает метрики качества по всему корпусу и готовит ОБЕЗЛИЧЕННУЮ выборку
для последующего разбора моделью.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import paths  # noqa: E402  — единая точка правды по путям (переносимо между машинами)
import json, os, re, glob, io, sys, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SRC = paths.DIALOGS
OUT = paths.ANALYSIS
os.makedirs(OUT, exist_ok=True)

# ---- регулярки для обезличивания и признаков ----
RE_PHONE = re.compile(r"(?:\+?7|8)?[\s\-\(\)]*\d{3}[\s\-\(\)]*\d{3}[\s\-\(\)]*\d{2}[\s\-\(\)]*\d{2}")
RE_EMAIL = re.compile(r"[\w\.\-]+@[\w\.\-]+\.\w+")
RE_URL   = re.compile(r"https?://\S+")
RE_IMG   = re.compile(r"🖼\s*\S+")
RE_PRICE = re.compile(r"\d[\d\s]{1,7}\s*(?:р\b|р\.|руб|₽|тыс|т\.р)", re.I)

CAT_KEYS = {
    "mnc": ["мебел", "кухн", "сборк", "сантех", "электрик", "муж на час", "ремонт кварт",
             "плинтус", "карниз", "полк", "навес", "столешн", "двер"],
    "bt":  ["стиральн", "холодильн", "посудомоеч", "плита", "духов", "варочн", "телевизор",
             "бытов", "микроволнов", "кондицион", "водонагрев"],
    "kp":  ["компьютер", "ноутбук", "пк ", "windows", "виндовс", "интернет", "роутер",
             "программ", "вирус", "переустанов"],
}

def redact(t):
    if not t: return ""
    t = RE_IMG.sub("<ФОТО>", t)
    t = RE_URL.sub("<ССЫЛКА>", t)
    t = RE_EMAIL.sub("<EMAIL>", t)
    t = RE_PHONE.sub("<ТЕЛЕФОН>", t)
    return t

def guess_cat(meta):
    blob = " ".join([str(meta.get("page", {}).get("title", "")),
                     str(meta.get("page", {}).get("url", ""))]).lower()
    for cat, keys in CAT_KEYS.items():
        if any(k in blob for k in keys):
            return cat
    return "unknown"

def source_of(meta):
    url = (meta.get("page", {}) or {}).get("url", "") or ""
    v = meta.get("visitor", {}) or {}
    socials = ((v.get("social") or {}).get("socialProfiles") or [])
    if socials:
        tn = socials[0].get("typeName", "")
        if tn == "av": return "avito"
        return tn or "widget"
    if "avito" in url: return "avito"
    if url: return "site"
    return "unknown"

files = sorted(glob.glob(os.path.join(SRC, "*.json")))
stats = collections.Counter()
by_source = collections.Counter()
by_cat = collections.Counter()
by_agent = collections.Counter()
finish = collections.Counter()
msgcounts = []
sample = []          # обезличенные двусторонние диалоги для модели
lost_examples = 0

for fp in files:
    try:
        d = json.load(open(fp, encoding="utf-8"))
    except Exception:
        stats["broken"] += 1
        continue
    msgs = d.get("messages", [])
    meta = d.get("meta", {}) or {}
    stats["total"] += 1
    finish[d.get("finish_reason", "?")] += 1
    n_client = sum(1 for m in msgs if m.get("role") == "client")
    n_op = sum(1 for m in msgs if m.get("role") == "operator")
    msgcounts.append(len(msgs))
    src = source_of(meta); cat = guess_cat(meta)
    by_source[src] += 1; by_cat[cat] += 1
    for a in meta.get("agents", []):
        by_agent[a.get("name", "?")] += 1

    if n_client == 0:
        stats["no_client"] += 1
    if n_op == 0:
        stats["no_operator_reply"] += 1
    if n_client and n_op:
        stats["two_sided"] += 1

    client_text = " \n".join(m.get("text", "") for m in msgs if m.get("role") == "client")
    op_text     = " \n".join(m.get("text", "") for m in msgs if m.get("role") == "operator")

    # признаки качества
    if RE_PHONE.search(client_text): stats["phone_collected"] += 1
    if RE_PRICE.search(op_text): stats["operator_named_price"] += 1

    # потерянный лид: последним говорил оператор и это вопрос, клиент не ответил
    if msgs and msgs[-1].get("role") == "operator" and msgs[-1].get("text", "").strip().endswith(("?", "?:")):
        stats["lost_after_operator_q"] += 1
        lost_examples += 1

    # копим обезличенную выборку двусторонних диалогов
    if n_client and n_op and 4 <= len(msgs) <= 60:
        red = [{"role": m.get("role"), "text": redact(m.get("text", ""))} for m in msgs
               if m.get("text")]
        sample.append({
            "key": d.get("conversation_key"),
            "source": src, "cat": cat,
            "agent": (meta.get("agents", [{}]) or [{}])[0].get("name", "?"),
            "n": len(msgs),
            "messages": red,
        })

# ---- вывод ----
def pct(n):
    t = stats["total"] or 1
    return "%d (%.0f%%)" % (n, 100.0*n/t)

print("="*60)
print("СТРУКТУРНЫЙ РАЗБОР ДИАЛОГОВ JIVO")
print("="*60)
print("Всего файлов-диалогов:", stats["total"], " | битых:", stats["broken"])
print("Двусторонних (клиент+оператор):", pct(stats["two_sided"]))
print("Без ответа оператора вообще:", pct(stats["no_operator_reply"]))
print("Без реплик клиента:", pct(stats["no_client"]))
print()
print("Собран телефон клиента:", pct(stats["phone_collected"]))
print("Оператор НАЗВАЛ цену в чате:", pct(stats["operator_named_price"]))
print("Оборвалось на вопросе оператора (клиент молчит):", pct(stats["lost_after_operator_q"]))
if msgcounts:
    msgcounts.sort()
    print("Сообщений в диалоге: медиана %d, макс %d" % (msgcounts[len(msgcounts)//2], msgcounts[-1]))
print()
print("Источники:", dict(by_source.most_common()))
print("Направления (по объявлению):", dict(by_cat.most_common()))
print("Операторы:", dict(by_agent.most_common(10)))
print("finish_reason:", dict(finish.most_common()))

# сохраняем выборку и агрегаты
json.dump({"stats": dict(stats), "by_source": dict(by_source),
           "by_cat": dict(by_cat), "by_agent": dict(by_agent),
           "finish": dict(finish)},
          open(os.path.join(OUT, "stats.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

# перемешать детерминированно (по ключу) и отобрать до 60 для модели
sample.sort(key=lambda x: str(x["key"]))
with open(os.path.join(OUT, "sample_redacted.jsonl"), "w", encoding="utf-8") as f:
    for s in sample:
        f.write(json.dumps(s, ensure_ascii=False) + "\n")
print()
print("Обезличенная выборка двусторонних диалогов:", len(sample), "-> analysis/sample_redacted.jsonl")
print("Агрегаты -> analysis/stats.json")
