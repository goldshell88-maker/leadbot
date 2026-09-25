# -*- coding: utf-8 -*-
"""Разбор разметки заказчика со страницы /review: собирает все 👎, правки текста
и комментарии в один отчёт review_feedback_report.md — по нему вносятся правки
плейбука/роутеров. Запуск:  py analysis\\analyze_review_marks.py"""
import sys, io, os, json, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    p = os.path.join(HERE, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def main():
    data = load("review_dialogs.json")
    marks = load("review_marks.json")
    dialogs = {d["id"]: d for d in data.get("dialogs", [])}
    verd = collections.Counter()
    votes = collections.Counter()
    findings = []           # (важность, диалог, блок текста)
    for did, m in marks.items():
        d = dialogs.get(did)
        if not d:
            continue
        v = m.get("verdict")
        if v:
            verd[v] += 1
        for idx_s, mm in (m.get("msgs") or {}).items():
            if not mm:
                continue
            try:
                idx = int(idx_s)
                msg = d["messages"][idx]
            except (ValueError, IndexError):
                continue
            if mm.get("vote"):
                votes[mm["vote"]] += 1
            has_edit = mm.get("edit") is not None and mm.get("edit") != msg.get("text")
            if mm.get("vote") == "down" or has_edit:
                ctx = ""
                for k in range(idx - 1, -1, -1):
                    if d["messages"][k]["role"] == "client":
                        ctx = d["messages"][k]["text"]
                        break
                block = ["### %s — %s (реплика #%d)" % (did, d.get("title", ""), idx),
                         "Клиент: %s" % ctx[:300],
                         "Бот: %s" % (msg.get("text") or "(пусто)")]
                if mm.get("vote") == "down":
                    block.append("Оценка: 👎")
                if has_edit:
                    block.append("КАК НАДО (правка заказчика): %s" % mm["edit"])
                if mm.get("regen") is not None:
                    block.append("(перегенерирован: %s)" % mm["regen"][:200])
                findings.append((0 if has_edit else 1, did, "\n".join(block)))
        if v == "bad" or (m.get("comment") or "").strip():
            findings.append((2, did, "### %s — %s\nВердикт: %s\nКомментарий: %s"
                             % (did, d.get("title", ""), v or "-", m.get("comment", ""))))
    findings.sort(key=lambda x: (x[0], x[1]))
    out = os.path.join(HERE, "review_feedback_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("# Разметка заказчика: страница /review\n\n")
        f.write("Диалогов размечено: %d из %d. Вердикты: %s. Оценки реплик: %s\n\n"
                % (len(marks), len(dialogs), dict(verd), dict(votes)))
        f.write("## Сначала правки текста (готовые эталоны), затем 👎, затем комментарии\n\n")
        for _, _, b in findings:
            f.write(b + "\n\n")
    print("Вердикты: %s | Оценки реплик: %s | находок: %d" % (dict(verd), dict(votes), len(findings)))
    print("-> review_feedback_report.md")


if __name__ == "__main__":
    main()
