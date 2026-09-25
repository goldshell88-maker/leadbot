# -*- coding: utf-8 -*-
"""РЕЖИМ ОБУЧЕНИЯ: бот проходит живой диалог ЦЕЛИКОМ, и каждый ход разбирается.

ЗАЧЕМ. Владелец не пускает бота в бой, а в подсказках видит только ПЕРВЫЕ ответы:
дальше срабатывает заслон «диалог уже ведёт оператор» (он правилен для боя — в 65 %
живых ходов бот влезал в чужой разговор). Разбирать бота на одних первых ходах
нельзя: не видно, как он ведёт диалог к заявке и где ломается.

ЧТО ДЕЛАЕТ. Берёт настоящий диалог, где отвечал ЖИВОЙ оператор, и на каждом ходе
клиента спрашивает бота, что бы он ответил ЗДЕСЬ. Заслон при этом снят (переменная
LEADBOT_TRAINING) — только для разбора, в бою он остаётся. Потом пара «оператор против
бота» уходит на разбор по промпту из analysis/PROMPT-РАЗБОР.md.

⚠ БОТ НЕ ВИДИТ БУДУЩЕГО. На каждом ходе ему подаётся история РОВНО ДО этого момента,
как было бы в бою. Иначе разбор мерил бы ясновидение, а не работу.

⚠ ПЛАТНО. На диалог: по вызову модели на каждый ход клиента плюс один разбор.
Диалог из 4 ходов ≈ 4 ₽. Кэш ответов уводится в свой файл, боевой не растёт.

Запуск:
    uv run python analysis/train_mode.py [сколько=5] [--из-боя]
      без ключа   — диалоги из корпуса (analysis/LEARNING/corpus.jsonl)
      --из-боя    — диалоги из выгрузки панели (analysis/_train_live.jsonl)
Итог: analysis/РАЗБОР-ОБУЧЕНИЕ.md
"""
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ⚠ ДВЕ ПЕРЕМЕННЫЕ СТАВЯТСЯ ДО ИМПОРТА server: заслон читается на импорте, а кэш —
# при первом обращении. Поставить их ниже значит не поставить вовсе.
os.environ["LEADBOT_TRAINING"] = "1"
os.environ.setdefault("LEADBOT_ANSWER_CACHE",
                      os.path.join(КОРЕНЬ, "analysis", "_train_cache.jsonl"))
sys.path.insert(0, os.path.join(КОРЕНЬ, "brain"))
import claude_api                                        # noqa: E402
import server                                            # noqa: E402

КОРПУС = os.path.join(КОРЕНЬ, "analysis", "LEARNING", "corpus.jsonl")
ЖИВЫЕ = os.path.join(КОРЕНЬ, "analysis", "_train_live.jsonl")
ПРОМПТ = os.path.join(КОРЕНЬ, "analysis", "PROMPT-РАЗБОР.md")
ИТОГ = os.path.join(КОРЕНЬ, "analysis", "РАЗБОР-ОБУЧЕНИЕ.md")

ОТКРЫВАЕТ_RX = re.compile(
    r"(?i)^\s*(здравствуйте|добрый|доброе|приветствую)|стиральн|стиралк|холодильник|"
    r"телевизор|посудомо|плит[аыу]|духов|ноутбук|компьютер|принтер|роутер|мебел|шкаф|"
    r"повесить|собрать|установ|подключ|заменить|починить|отремонт|скольк|стоимост")


def из_корпуса(сколько):
    """Диалоги, где клиент ОТКРЫЛ разговор и оператор вёл его не меньше трёх ходов."""
    из = []
    for line in io.open(КОРПУС, encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        msgs = [{"role": ("client" if (m.get("role") or "") == "client" else "operator"),
                 "text": (m.get("text") or "").strip()}
                for m in (d.get("messages") or []) if (m.get("text") or "").strip()]
        if len(msgs) < 6 or msgs[0]["role"] != "client":
            continue
        if not ОТКРЫВАЕТ_RX.search(msgs[0]["text"]):
            continue
        ходов = sum(1 for m in msgs if m["role"] == "operator")
        if ходов < 3:
            continue
        город = (((d.get("meta") or {}).get("visitor") or {}).get("city")
                 or (d.get("meta") or {}).get("city") or "Москва")
        из.append({"город": город, "реплики": msgs[:12]})
        if len(из) >= сколько * 6:
            break
    шаг = max(1, len(из) // сколько)
    return из[::шаг][:сколько]


def из_боя(сколько):
    if not os.path.exists(ЖИВЫЕ):
        print("нет файла %s — выгрузите диалоги из панели" % ЖИВЫЕ)
        return []
    из = [json.loads(l) for l in io.open(ЖИВЫЕ, encoding="utf-8") if l.strip()]
    return из[:сколько]


def прогнать(диалог):
    """Пара «что ответил оператор / что ответил бы бот» на каждом ходе клиента."""
    ходы = []
    история = []
    for i, m in enumerate(диалог["реплики"]):
        if m["role"] != "client":
            история.append(m)
            continue
        история.append(m)
        # ответ оператора — следующая его реплика, если она есть
        оператор = ""
        for сл in диалог["реплики"][i + 1:]:
            if сл["role"] == "operator":
                оператор = сл["text"]
                break
            if сл["role"] == "client":
                break
        if not оператор:
            continue
        # ⚠ ИСТОРИЯ РОВНО ДО ЭТОГО МОМЕНТА — бот не должен видеть будущего
        r = server.run_relay([dict(x) for x in история],
                             known={"city": диалог.get("город") or "Москва",
                                    "visitor": "", "title": ""})
        ходы.append({"клиент": m["text"],
                     "оператор": оператор,
                     "бот": (r.get("reply_text") or r.get("reply") or "").strip(),
                     "передал": bool(r.get("notify_human")),
                     "причина": (r.get("escalation") or {}).get("reason") or ""})
    return ходы


def разобрать(диалог, ходы, промпт):
    куски = ["## Диалог (город: %s)" % (диалог.get("город") or "не указан"), ""]
    for n, х in enumerate(ходы, 1):
        куски.append("### Ход %d" % n)
        куски.append("КЛИЕНТ: %s" % х["клиент"])
        куски.append("ОПЕРАТОР (живой): %s" % х["оператор"])
        куски.append("БОТ: %s" % (х["бот"] or "(промолчал, отдал диалог человеку%s)"
                                  % ((", причина: " + х["причина"]) if х["причина"] else "")))
        куски.append("")
    текст = промпт + "\n\n---\n\n" + "\n".join(куски)
    try:
        ответ = claude_api.messages(
            system="Отвечай по-русски, по структуре из задания. Без общих фраз.",
            msgs=[{"role": "user", "content": текст}],
            model="claude-sonnet-5", max_tokens=4000)
        return "".join(b.get("text", "") for b in (ответ.get("content") or []))
    except Exception as e:      # разбор не должен ронять прогон
        return "_разбор не получен: %s_" % e


def главное():
    сколько = 5
    for a in sys.argv[1:]:
        if a.isdigit():
            сколько = int(a)
    диалоги = из_боя(сколько) if "--из-боя" in sys.argv else из_корпуса(сколько)
    if not диалоги:
        return 1
    промпт = io.open(ПРОМПТ, encoding="utf-8").read()
    части = ["# Разбор обучения: бот против живого оператора", "",
             "Собрано `analysis/train_mode.py`. Заслон «чужой диалог» снят ТОЛЬКО для",
             "разбора — в бою он работает. Бот на каждом ходе видел историю ровно до",
             "этого момента, будущего не знал.", ""]
    for i, d in enumerate(диалоги, 1):
        print("диалог %d из %d…" % (i, len(диалоги)), flush=True)
        ходы = прогнать(d)
        if not ходы:
            continue
        части.append("\n---\n\n## Диалог %d (город: %s, ходов: %d)"
                     % (i, d.get("город") or "?", len(ходы)))
        for n, х in enumerate(ходы, 1):
            части.append("\n**Ход %d**" % n)
            части.append("\n* КЛИЕНТ: %s" % х["клиент"].replace("\n", " "))
            части.append("* ОПЕРАТОР: %s" % х["оператор"].replace("\n", " "))
            части.append("* БОТ: %s" % ((х["бот"] or "(промолчал)").replace("\n", " ")))
        части.append("\n### Разбор\n")
        части.append(разобрать(d, ходы, промпт))
    io.open(ИТОГ, "w", encoding="utf-8").write("\n".join(части))
    print("\nготово: %s" % ИТОГ)
    return 0


if __name__ == "__main__":
    sys.exit(главное())
