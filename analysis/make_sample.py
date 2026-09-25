# -*- coding: utf-8 -*-
"""Готовит читаемую стратифицированную выборку диалогов для ручного разбора
(локально, без сети). Пишет analysis/sample_for_review.txt.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import paths  # noqa: E402  — единая точка правды по путям (переносимо между машинами)
import json, os, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
OUT = paths.ANALYSIS
RE_PRICE = re.compile(r"\d[\d\s]{1,7}\s*(?:р\b|р\.|руб|₽|тыс|т\.р)", re.I)

rows = [json.loads(l) for l in open(os.path.join(OUT, "sample_redacted.jsonl"), encoding="utf-8")]
for d in rows:
    ctext = " ".join(m["text"] for m in d["messages"] if m["role"] == "client")
    otext = " ".join(m["text"] for m in d["messages"] if m["role"] == "operator")
    last = d["messages"][-1] if d["messages"] else {}
    d["_phone"] = "<ТЕЛЕФОН>" in ctext
    d["_price"] = bool(RE_PRICE.search(otext))
    d["_lost"] = last.get("role") == "operator" and last.get("text", "").strip().endswith(("?", "?:"))

def take(pred, n, used):
    out = []
    for d in rows:
        if d["key"] in used: continue
        if pred(d):
            out.append(d); used.add(d["key"])
            if len(out) >= n: break
    return out

used = set()
groups = [
    ("ПОТЕРЯННЫЕ ЛИДЫ (оборвалось на вопросе оператора, телефон НЕ взят)",
     take(lambda d: d["_lost"] and not d["_phone"], 14, used)),
    ("УСПЕШНЫЕ (телефон всё-таки взяли)",
     take(lambda d: d["_phone"], 12, used)),
    ("НАЗВАЛИ ЦЕНУ В ЧАТЕ",
     take(lambda d: d["_price"], 6, used)),
    ("РАЗНЫЕ",
     take(lambda d: True, 8, used)),
]

lines = []
for title, ds in groups:
    lines.append("\n\n########## %s (%d) ##########" % (title, len(ds)))
    for d in ds:
        lines.append("\n===== key=%s | направление=%s | оператор=%s | телефон=%s цена=%s оборван=%s ====="
                     % (d["key"], d["cat"], d["agent"], d["_phone"], d["_price"], d["_lost"]))
        for m in d["messages"][:30]:
            who = "КЛИЕНТ  " if m["role"] == "client" else "ОПЕРАТОР"
            lines.append("%s | %s" % (who, m["text"].replace("\n", " ⏎ ")))

open(os.path.join(OUT, "sample_for_review.txt"), "w", encoding="utf-8").write("\n".join(lines))
print("Готово: analysis/sample_for_review.txt |", sum(len(ds) for _, ds in groups), "диалогов")
