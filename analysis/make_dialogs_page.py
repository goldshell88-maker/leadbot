# -*- coding: utf-8 -*-
"""Страница «Полные диалоги» — витрина того, как бот разговаривает на самом деле.

Берёт фактические результаты analysis/test_full_dialogs.py (не переписанные вручную!)
и собирает самодостаточный HTML в том же оформлении, что и отчёт руководству.
Открывается на панели бота: /docs → ПОЛНЫЕ-ДИАЛОГИ.html

Запуск: python3 analysis/make_dialogs_page.py
"""
import html
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
MODE = (sys.argv[1] if len(sys.argv) > 1 else "full")     # full | gold50
if MODE == "gold50":
    SRC = os.path.join(HERE, "_gold50.jsonl")
    OUT = os.path.join(HERE, "ЭТАЛОН-50-ДИАЛОГОВ.html")
    TITLE = "Лид-бот — эталон-50"
    HEAD = "Пятьдесят диалогов целиком"
    SUB = ("Все направления, все типы клиентов, все исходы · "
           "фактический вывод прогона, а не написанные вручную примеры")
else:
    SRC = os.path.join(HERE, "_full_dialogs_results.jsonl")
    OUT = os.path.join(HERE, "ПОЛНЫЕ-ДИАЛОГИ.html")
    TITLE = "Лид-бот — полные диалоги"
    HEAD = "Как бот разговаривает целиком"
    SUB = ("Сквозные диалоги от первого слова до записи · "
           "не примеры «как надо», а фактический вывод тестов")


def e(s):
    return html.escape(str(s or ""))


CSS = """*{box-sizing:border-box;margin:0}
body{font:15px/1.65 -apple-system,'Segoe UI',system-ui,Roboto,sans-serif;color:#1a2b26;
  background:#f5f7f6;padding:0 0 60px}
.wrap{max-width:900px;margin:0 auto;padding:0 22px}
header{background:linear-gradient(135deg,#0f2f26,#123f33);color:#eaf6f1;padding:34px 0 28px;margin-bottom:24px}
header h1{font-size:27px;letter-spacing:.3px}
header .sub{color:#8fc9b5;font-size:14px;margin-top:6px}
h2{font-size:18px;margin:34px 0 4px;padding-bottom:8px;border-bottom:2px solid #d9e5e0}
h2 .n{color:#2f9e79;font-weight:800;margin-right:9px}
h3{font-size:15px;margin:20px 0 4px;color:#12463a}
h3 .n{color:#2f9e79;font-weight:800;margin-right:8px}
.meta{font-weight:400;font-size:12.5px;color:#7d968f}
p{margin:9px 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:16px 0}
.kpi{background:#fff;border:1px solid #dde8e4;border-radius:12px;padding:14px 16px}
.kpi .v{font-size:24px;font-weight:800;color:#1f8a63;line-height:1.2}
.kpi .l{font-size:12.5px;color:#63807a;margin-top:3px}
.dlg{background:#fff;border:1px solid #dde8e4;border-radius:12px;padding:14px 16px;margin:12px 0}
.m{margin:7px 0;padding:9px 13px;border-radius:11px;white-space:pre-wrap;font-size:14px;max-width:86%}
.m b{display:block;font-size:11px;letter-spacing:.5px;text-transform:uppercase;margin-bottom:3px;opacity:.6}
.m.c{background:#eef2f1;color:#243a34}
.m.b{background:#e6f6ef;color:#0f3d31;margin-left:auto;border:1px solid #cfe9dd}
.chk{font-size:12px;color:#4d7d6a;margin:2px 0 10px auto;max-width:86%;text-align:right}
.flag{font-size:11px;color:#c07c17;font-weight:600}
.note{background:#eef9f3;border-left:4px solid #2f9e79;padding:12px 15px;border-radius:0 10px 10px 0;margin:14px 0;font-size:14px}
footer{margin-top:40px;padding-top:16px;border-top:1px solid #dde8e4;color:#7d968f;font-size:13px}
@media print{body{background:#fff}.dlg{break-inside:avoid}h2{break-after:avoid}}"""


def main():
    if not os.path.exists(SRC):
        print("Нет результатов: сначала запустите analysis/%s"
              % ("gold50.py" if MODE == "gold50" else "test_full_dialogs.py"))
        return 1
    data = [json.loads(l) for l in open(SRC, encoding="utf-8") if l.strip()]
    turns = sum(len(d["rows"]) for d in data)
    clean = sum(1 for d in data
                if not d["cross"] and d.get("outcome_ok", True))

    o = io.StringIO()
    w = o.write
    w('<!doctype html><html lang="ru"><head><meta charset="utf-8">'
      '<meta name="viewport" content="width=device-width,initial-scale=1">'
      '<title>%s</title><style>%s</style></head><body>' % (TITLE, CSS))
    w('<header><div class="wrap"><h1>%s</h1><div class="sub">%s</div></div></header>'
      '<div class="wrap">' % (e(HEAD), e(SUB)))
    w('<div class="kpis">')
    for v, l in ((len(data), "диалогов от начала до записи"), (turns, "ходов проверено"),
                 ("%d из %d" % (clean, len(data)), "без единого нарушения")):
        w('<div class="kpi"><div class="v">%s</div><div class="l">%s</div></div>' % (e(v), e(l)))
    w('</div>')
    w('<div class="note"><b>Что здесь проверяется.</b> Не отдельные реплики, а диалог целиком: '
      'поздоровался один раз, сначала выяснил поломку и только потом спрашивает адрес, не '
      'пересказывает проблему клиента, не роняет разговор без вопроса и доводит до записи. '
      'Диалоги ниже — реальный вывод прогона, включая провокации и конфликт посреди заявки.</div>')

    group = None
    for i, d in enumerate(data, 1):
        if d.get("group") and d["group"] != group:
            group = d["group"]
            w('<h2><span class="n">§</span>%s</h2>' % e(group))
        head = e(d["id"])
        if d.get("city"):
            head += ' <span class="meta">· %s · исход: %s</span>' % (
                e(d["city"]), e(d.get("kind") or ""))
        w('<h3>%s%s</h3>' % ("" if d.get("group") else '<span class="n">%d</span>' % i, head))
        w('<div class="dlg">')
        for r in d["rows"]:
            w('<div class="m c"><b>Клиент</b>%s</div>' % e(r["client"]))
            w('<div class="m b"><b>Бот</b>%s%s</div>'
              % (e(r["bot"]) if (r["bot"] or "").strip() else "<i>молчит — оператору</i>",
                 '<br><span class="flag">⚑ диалог помечен оператору</span>' if r.get("handoff") else ""))
            if r.get("what"):
                w('<div class="chk">✓ %s</div>' % e(r["what"]))
        w('</div>')

    runner = "gold50.py" if MODE == "gold50" else "test_full_dialogs.py"
    w('<footer>Собрано из %s · пересобрать: python3 analysis/%s &amp;&amp; '
      'python3 analysis/make_dialogs_page.py %s</footer></div></body></html>'
      % (e(os.path.basename(SRC)), e(runner), e(MODE)))

    open(OUT, "w", encoding="utf-8").write(o.getvalue())
    print("Готово: %s (%.1f КБ, диалогов %d, ходов %d)"
          % (os.path.basename(OUT), len(o.getvalue()) / 1024, len(data), turns))
    return 0


if __name__ == "__main__":
    sys.exit(main())
