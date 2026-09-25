# -*- coding: utf-8 -*-
"""Качественный разбор выборки диалогов моделью Claude.

Берёт обезличенную выборку (sample_redacted.jsonl), отбирает репрезентативные
диалоги (потерянные лиды, успешные, с ценой), гоняет батчами через Claude и
собирает структурный разбор: ошибки операторов, нарушения регламента,
удачные приёмы, оценка. Итог -> quality_report.json.
"""
import paths  # noqa: E402  — единая точка правды по путям
import json, os, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import claude_api

OUT = paths.ANALYSIS
RE_PRICE = re.compile(r"\d[\d\s]{1,7}\s*(?:р\b|р\.|руб|₽|тыс|т\.р)", re.I)

rows = [json.loads(l) for l in open(os.path.join(OUT, "sample_redacted.jsonl"), encoding="utf-8")]

def flags(d):
    ctext = " ".join(m["text"] for m in d["messages"] if m["role"] == "client")
    otext = " ".join(m["text"] for m in d["messages"] if m["role"] == "operator")
    last = d["messages"][-1] if d["messages"] else {}
    return {
        "phone": "<ТЕЛЕФОН>" in ctext,
        "price": bool(RE_PRICE.search(otext)),
        "lost": last.get("role") == "operator" and last.get("text", "").strip().endswith(("?", "?:")),
    }

for d in rows:
    d["flags"] = flags(d)

# стратифицированная выборка ~42 диалога
def take(pred, n, used):
    out = []
    for d in rows:
        if d["key"] in used: continue
        if pred(d):
            out.append(d); used.add(d["key"])
            if len(out) >= n: break
    return out

used = set()
picked  = take(lambda d: d["flags"]["lost"] and not d["flags"]["phone"], 16, used)   # потерянные без телефона
picked += take(lambda d: d["flags"]["phone"], 12, used)                               # где телефон всё же взяли
picked += take(lambda d: d["flags"]["price"], 6, used)                                # где назвали цену
picked += take(lambda d: True, 8, used)                                               # просто разные
print("В разбор отобрано:", len(picked), "диалогов")

def transcript(d):
    lines = []
    for m in d["messages"]:
        who = "КЛИЕНТ" if m["role"] == "client" else "ОПЕРАТОР"
        lines.append("%s: %s" % (who, m["text"]))
    return "\n".join(lines)

TOOL = {
    "name": "report_batch",
    "description": "Разбор качества батча диалогов диспетчеров.",
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "outcome": {"type": "string", "enum": ["won", "lost", "unclear"],
                                     "description": "won=довели до заявки/контакта, lost=слили, unclear"},
                        "phone_captured": {"type": "boolean"},
                        "score": {"type": "integer", "description": "Качество работы оператора 1-5"},
                        "violations": {"type": "array", "items": {"type": "string"},
                                        "description": "Нарушения регламента/ошибки оператора, кратко"},
                        "good_moves": {"type": "array", "items": {"type": "string"},
                                        "description": "Что оператор сделал хорошо"},
                        "one_line": {"type": "string", "description": "Итог одной фразой"},
                    },
                    "required": ["key", "outcome", "phone_captured", "score", "violations", "one_line"],
                },
            },
        },
        "required": ["items"],
    },
}

SYSTEM = """Ты — руководитель отдела контроля качества колл-центра. Компания принимает
заявки с Avito на бытовые услуги (сборка/ремонт мебели, ремонт техники, компьютерная помощь,
муж на час) и общается с клиентами в чате Jivo. Цель диспетчера в чате — быстро, вежливо и
по делу довести обращение до ЗАЯВКИ: понять задачу, ОБЯЗАТЕЛЬНО взять телефон, адрес и
удобное время, назначить выезд мастера. Цену в чате точную не называть — «мастер сориентирует
на месте после осмотра». Нельзя бросать клиента без ответа и задавать вопрос, а потом молчать.

Разбери каждый диалог строго и по делу: выигран лид или слит, взяли ли телефон, какие ошибки
и нарушения, что было хорошо, оценка 1-5. Пиши кратко, по-русски, конкретными формулировками."""

items_all = []
B = 6
batches = [picked[i:i+B] for i in range(0, len(picked), B)]
for bi, batch in enumerate(batches, 1):
    body = "\n\n".join("=== ДИАЛОГ key=%s | направление=%s | оператор=%s ===\n%s"
                       % (d["key"], d["cat"], d["agent"], transcript(d)) for d in batch)
    user = "Разбери эти %d диалогов, верни report_batch:\n\n%s" % (len(batch), body)
    try:
        resp = claude_api.messages(
            system=SYSTEM,
            msgs=[{"role": "user", "content": user}],
            tools=[TOOL], tool_choice={"type": "tool", "name": "report_batch"},
            model="claude-sonnet-5", max_tokens=3000,
        )
        out = claude_api.first_tool_input(resp, "report_batch") or {}
        got = out.get("items", [])
        items_all.extend(got)
        u = resp.get("usage", {})
        print("Батч %d/%d: разобрано %d | токены %s/%s" %
              (bi, len(batches), len(got), u.get("input_tokens"), u.get("output_tokens")))
    except claude_api.ClaudeError as e:
        print("Батч %d: ОШИБКА %s" % (bi, e))

# агрегаты
won = sum(1 for x in items_all if x.get("outcome") == "won")
lost = sum(1 for x in items_all if x.get("outcome") == "lost")
phone = sum(1 for x in items_all if x.get("phone_captured"))
avg = sum(x.get("score", 0) for x in items_all) / (len(items_all) or 1)
viol = {}
for x in items_all:
    for v in x.get("violations", []):
        viol[v.strip()] = viol.get(v.strip(), 0) + 1

report = {"n": len(items_all), "won": won, "lost": lost, "phone_captured": phone,
          "avg_score": round(avg, 2), "items": items_all}
json.dump(report, open(os.path.join(OUT, "quality_report.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

print("\n" + "="*60)
print("ИТОГ РАЗБОРА ВЫБОРКИ (%d диалогов)" % len(items_all))
print("="*60)
print("Выиграно/Слито/Неясно: %d / %d / %d" % (won, lost, len(items_all)-won-lost))
print("Телефон взят: %d из %d" % (phone, len(items_all)))
print("Средняя оценка операторов: %.2f из 5" % avg)
print("\nТоп повторяющихся ошибок:")
for v, c in sorted(viol.items(), key=lambda kv: -kv[1])[:15]:
    print("  %2d× %s" % (c, v))
print("\n-> analysis/quality_report.json")
