# -*- coding: utf-8 -*-
"""Разбор ТОЛЬКО новейших диалогов (по ts последнего сообщения) — ищем НОВЫЕ паттерны/пробелы.

Источник — постоянное хранилище dialogs_merged (живая dialogs/ пустеет после ежечасного мерджа)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import paths  # noqa: E402  — единая точка правды по путям (переносимо между машинами)
import sys, io, os, re, glob, json, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SRC = paths.MERGED
OUT = paths.ANALYSIS
N = int(sys.argv[1]) if len(sys.argv) > 1 else 300

RE_PHONE = re.compile(r"(?:\+?7|8)?[\s\-\(\)]*\d{3}[\s\-\(\)]*\d{3}[\s\-\(\)]*\d{2}[\s\-\(\)]*\d{2}")
RE_URL = re.compile(r"https?://\S+")
RX_Q = re.compile(r"[^.!?\n]*\?")


def redact(t):
    t = RE_URL.sub("<ссылка>", t or "")
    t = RE_PHONE.sub("<тел>", t)
    return t


def normq(t):
    t = redact(t).lower().strip()
    t = re.sub(r"\s+", " ", t)
    return t.strip(" ?!.,-—«»\"")


def last_msg_ts(d):
    for m in reversed(d.get("messages") or []):
        if m.get("ts"):
            return m["ts"]
    return 0


loaded = []
for fp in glob.glob(os.path.join(SRC, "*.json")):
    try:
        d = json.load(open(fp, encoding="utf-8"))
    except Exception:
        continue
    loaded.append((last_msg_ts(d), d))
loaded.sort(key=lambda x: x[0])
loaded = loaded[-N:]   # новейшие по времени последнего сообщения
print("Новейших диалогов взято:", len(loaded))
first_msgs = collections.Counter()
questions = collections.Counter()
sample = []
for _, d in loaded:
    msgs = d.get("messages", []) or []
    cl = [m for m in msgs if m.get("role") == "client"]
    op = [m for m in msgs if m.get("role") == "operator"]
    if cl:
        fm = normq(cl[0].get("text", ""))
        if fm:
            first_msgs[fm[:90]] += 1
    for m in cl:
        for q in RX_Q.findall(m.get("text", "") or ""):
            qn = normq(q)
            if 6 <= len(qn) <= 130 and not re.fullmatch(r"[a-z0-9_\-]{20,}", qn):
                questions[qn] += 1
    if cl and op and 4 <= len(msgs) <= 40:
        sample.append({
            "key": d.get("conversation_key"),
            "title": ((d.get("meta") or {}).get("page") or {}).get("title", "")[:60],
            "m": [{"r": ("К" if m.get("role") == "client" else "О"),
                   "t": redact(m.get("text", ""))} for m in msgs if m.get("text")],
        })

print("\nТОП-30 первых сообщений (новейшие):")
for t, c in first_msgs.most_common(30):
    print("  %2d  %s" % (c, t))
print("\nТОП-40 вопросов клиентов (новейшие):")
for q, c in questions.most_common(40):
    print("  %2d  %s" % (c, q))

# выборка для глубокого чтения (берём разнообразные — каждый 3-й)
sample = sample[::3][:60]
with open(os.path.join(OUT, "mine_recent_sample.jsonl"), "w", encoding="utf-8") as f:
    for s in sample:
        f.write(json.dumps(s, ensure_ascii=False) + "\n")
print("\nВыборка для чтения: %d диалогов -> mine_recent_sample.jsonl" % len(sample))
