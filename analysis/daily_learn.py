# -*- coding: utf-8 -*-
"""ПЕТЛЯ ОБУЧЕНИЯ: ежедневный разбор свежих диалогов «оператор vs бот».

Берёт диалоги из ПОСТОЯННОГО хранилища dialogs_merged (живая dialogs/ пустеет после
ежечасного мерджа — по ней искать нельзя). Свежесть = ts ПОСЛЕДНЕГО сообщения диалога.
Прогоняет бота рядом с реальным оператором (compare_dialog) и пишет ОТЧЁТ РАСХОЖДЕНИЙ —
только те ходы, где бот ответил заметно иначе. Их разбирает человек (или Claude) и
точечно правит плейбук. Запуск:  py analysis\\daily_learn.py [дней=1] [макс_диалогов=15]
"""
# ⚠ ПОРЯДОК СТРОК ЗДЕСЬ НЕСУЩИЙ (правка 24.08). `import paths` стоял ВЫШЕ, чем
# sys.path.insert, и скрипт не запускался своей же командой из шапки: в чистом процессе
# «ModuleNotFoundError: No module named 'paths'». Работал он только там, где brain уже
# оказался в пути по другой причине. Тот же порядок, что в build_rag_pool.py.
import sys, io, os, json, glob, time, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import paths  # noqa: E402  — единая точка правды по путям
import server  # noqa

SRC = paths.MERGED
HERE = os.path.dirname(os.path.abspath(__file__))
DAYS = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
MAXD = int(sys.argv[2]) if len(sys.argv) > 2 else 15

RE_URL = re.compile(r"https?://\S+")
RE_PHONE = re.compile(r"(?:\+?7|8)?[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}")


def redact(t):
    return RE_PHONE.sub("<тел>", RE_URL.sub("<ссылка>", t or ""))


def norm(t):
    return re.sub(r"\s+", " ", (t or "").lower().strip(" .!,?"))


def differs(op, bot):
    """Грубая мера «бот ответил заметно иначе»: нет пересечения ключевых слов."""
    a, b = set(norm(op).split()), set(norm(bot).split())
    if not a or not b:
        return bool(a) != bool(b)
    inter = len(a & b) / max(1, min(len(a), len(b)))
    return inter < 0.34


def last_msg_ts(d):
    for m in reversed(d.get("messages") or []):
        if m.get("ts"):
            return m["ts"]
    return 0


def main():
    cutoff = time.time() - DAYS * 86400
    fresh = []
    for fp in glob.glob(os.path.join(SRC, "*.json")):
        # mtime — дешёвый предфильтр (merge переписывает файл при каждом обновлении);
        # точная свежесть — по ts последнего сообщения внутри
        if os.path.getmtime(fp) < cutoff:
            continue
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        ts = last_msg_ts(d)
        if ts >= cutoff:
            fresh.append((ts, fp, d))
    fresh.sort(key=lambda x: -x[0])
    print("Свежих диалогов за %.1f дн: %d (беру до %d)" % (DAYS, len(fresh), MAXD))
    diffs, taken = [], 0
    for _, fp, d in fresh:
        if taken >= MAXD:
            break
        msgs = [{"role": m.get("role"), "text": redact(m.get("text", ""))}
                for m in (d.get("messages") or []) if (m.get("text") or "").strip()]
        cl = [m for m in msgs if m["role"] == "client"]
        op = [m for m in msgs if m["role"] == "operator"]
        if len(cl) < 2 or len(op) < 1:
            continue
        taken += 1
        try:
            pairs = server.compare_dialog(msgs, known=None, max_points=5)
        except Exception as e:
            print("  %s: ошибка %r" % (d.get("conversation_key"), e))
            continue
        for p in pairs:
            if p.get("operator") and differs(p["operator"], p.get("bot", "")):
                diffs.append({"key": d.get("conversation_key"),
                              "client": p["client"][:150], "operator": p["operator"][:150],
                              "bot": (p.get("bot") or "")[:150]})
    out = os.path.join(HERE, "daily_learn_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("# Расхождения оператор vs бот (свежие диалоги)\n\n")
        f.write("Диалогов разобрано: %d, расхождений: %d\n\n" % (taken, len(diffs)))
        for i, x in enumerate(diffs, 1):
            f.write("## %d. %s\nКлиент: %s\n\n- Оператор: %s\n- Бот: %s\n\n" % (
                i, x["key"], x["client"], x["operator"], x["bot"]))
    print("Разобрано %d диалогов, расхождений: %d -> daily_learn_report.md" % (taken, len(diffs)))
    print("Дальше: показать отчёт Claude («разбери daily_learn_report.md и предложи правки плейбука»).")


if __name__ == "__main__":
    main()
