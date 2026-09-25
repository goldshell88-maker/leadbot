# -*- coding: utf-8 -*-
"""Обновление отчёта руководству свежими результатами тестов.

Отчёт (analysis/ОТЧЁТ-РУКОВОДСТВУ-*.html) — самодостаточный HTML, он живёт в репозитории
и открывается на панели бота в разделе /docs. Этот скрипт освежает в нём то, что меняется
от прогона к прогону: версию боевого кода, цифры сюит и раздел «5.4 Провокации» — его
диалоги берутся напрямую из analysis/_provocations_results.jsonl, чтобы в отчёте всегда
лежали РЕАЛЬНЫЕ последние ответы бота, а не переписанные вручную.

Запуск: python3 analysis/update_report.py [хэш_коммита]
"""
import html
import io
import json
import os
import re
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, "ОТЧЁТ-РУКОВОДСТВУ-2026-08-04.html")
PROV = os.path.join(HERE, "_provocations_results.jsonl")

# какие сценарии показываем руководству и как подписываем
SHOW = [
    ("вы бот — с проверкой", "Прямой вопрос и проверка арифметикой"),
    ("флирт лёгкий", "Флирт в первом же сообщении"),
    ("флирт настойчивый", "Настойчивое приглашение на встречу"),
    ("оскорбление", "Прямое хамство в адрес мастера"),
    ("троллинг три хода", "Троллинг: три попытки развлечься подряд"),
    ("разведка зарплат", "Попытка выведать условия работы и выйти на руководство"),
    ("разведка устройства бизнеса", "Попытка вскрыть, что это не частник"),
    ("ребёнок балуется", "Детская шалость в чате"),
    ("пьяный", "Несвязная речь — бот вытягивает суть"),
    ("непристойность", "Непристойное предложение"),
    ("политика", "Втягивание в политику"),
    ("приветствие без поломки", "Голое «здравствуйте» — бот не зовёт на выезд вслепую"),
]

NOTE = (
    '<div class="note good-note"><b>Что изменилось после разбора.</b> Нашли главный признак '
    'бота: он спрашивал адрес и время, когда клиент ещё не сказал, что сломалось — «куда '
    'приехать?» в ответ на приветствие или шутку. Живой мастер сначала выясняет поломку. '
    'Теперь порядок жёсткий: реакция на сказанное → «что случилось?» → и только после этого '
    'адрес, время и телефон.</div>')


def e(s):
    return html.escape(str(s or ""))


def dialog(rows, note):
    out = ['<div class="dlg"><div class="dnote">%s</div>' % e(note)]
    for r in rows:
        if (r.get("client") or "").strip():
            out.append('<div class="m c"><b>Клиент</b>%s</div>' % e(r["client"].strip()))
        b = (r.get("bot") or "").strip()
        out.append('<div class="m b"><b>Бот</b>%s</div>'
                   % (e(b) if b else "<i>молчит — передаёт оператору</i>"))
    out.append("</div>")
    return "".join(out)


def main():
    if not os.path.exists(REPORT):
        print("Отчёт не найден: %s" % REPORT)
        return 1
    h = open(REPORT, encoding="utf-8").read()

    # ⚠ «версия боевого кода» — это последний коммит, изменивший МОЗГ бота, а не сам отчёт:
    # иначе штамп всегда отставал на один коммит (отчёт коммитится после кода).
    rev = sys.argv[1] if len(sys.argv) > 1 else subprocess.run(
        ["git", "log", "-1", "--format=%h", "--", "brain"], cwd=os.path.dirname(HERE),
        capture_output=True, text=True).stdout.strip()
    if rev:
        h = re.sub(r"версия боевого кода [0-9a-f]{7,}", "версия боевого кода %s" % rev, h)

    prov = {r["id"]: r for r in
            (json.loads(l) for l in open(PROV, encoding="utf-8") if l.strip())}
    checks = sum(len(r["verdicts"]) for r in prov.values())
    passed = sum(1 for r in prov.values() for v in r["verdicts"] if v["ok"])

    # цифра сюиты раскачки в таблице испытаний
    # число сюиты лежит внутри <span class="ok">N / N</span> — забираем всю обёртку
    h = re.sub(r"(Раскачка.*?<span class=\"ok\">)\s*\d+\s*/\s*\d+(\s*</span>)",
               lambda m: "%s%d / %d%s" % (m.group(1), passed, checks, m.group(2)),
               h, count=1, flags=re.S)
    h = re.sub(r"\d+ попыток вывести из роли", "%d попыток вывести из роли" % len(prov), h)

    # раздел 5.4 целиком пересобираем из фактических результатов
    start = h.index("<h3>5.4 ")
    end = h.index("<h2>", start)
    head = h[start:h.index("</p>", start) + 4]
    body = "".join(dialog(prov[k]["rows"], note) for k, note in SHOW if k in prov)
    h = h[:start] + head + NOTE + body + h[end:]

    open(REPORT, "w", encoding="utf-8").write(h)
    print("Отчёт обновлён: версия %s, раскачка %d/%d, диалогов в 5.4 — %d"
          % (rev or "?", passed, checks, sum(1 for k, _ in SHOW if k in prov)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
