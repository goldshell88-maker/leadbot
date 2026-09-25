# -*- coding: utf-8 -*-
"""Журнал решений: пишет причину, не пишет переписку. Офлайн, 0 токенов.
Запуск: python3 analysis/test_logbook.py

ЗАЧЕМ ЭТИ ПРОВЕРКИ. Журнал заводился ради ответа на «почему бот так ответил» —
и ровно там же лежит риск утечки: в `meta.lead` едет ТЕЛЕФОН клиента, а сам
ответ и вопрос клиента — это переписка. Оба попадают в журнал одной невнимательной
строкой `payload.update(meta)`, и заметить это по работе системы нельзя никак:
бот отвечает как отвечал, а телефоны копятся в логах на чужом сервере.

Вторая проверяемая вещь — сквозной `request_id`. Он придуман на стороне LeadChat
и возвращается нами в `meta`; по нему одно обращение находится в журналах ОБЕИХ
систем. Потеряй его — и «где потерялось» снова становится археологией по времени.
"""
import io
import json
import os
import sys
import contextlib

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "brain"))
import logbook                                      # noqa: E402

CASES = []


def chk(name, got, want):
    CASES.append((name, got, want))


def записать(fn, *args, **kwargs):
    """Строки, которые функция напечатала, — уже разобранными."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args, **kwargs)
    raw = buf.getvalue().strip()
    return raw, [json.loads(line) for line in raw.split("\n") if line.strip()]


ОТВЕТ = {
    "reply": "Ремонт холодильников делаем. Выезд мастера 500 ₽, назовите модель.",
    "confidence": 1.0,
    "needs_operator": False,
    "meta": {
        "layer": "локальный роутер",
        "flag": "price",
        "ms": 42,
        "request_id": "rq-from-leadchat",
        "lead_ready": True,
        # ⚠ Телефон клиента. Именно он не должен оказаться в журнале.
        "lead": {"phone": "+79161234567", "name": "Анна"},
        "warnings": ["воронка не увидит номер"],
    },
}

raw, rows = записать(logbook.answered, ОТВЕТ, source="leadchat", request_id="rq-from-leadchat", ms=42)
строка = rows[0]

chk("одна строка на один ответ", len(rows), 1)
chk("событие названо", строка.get("event"), "leadbot.answered")
chk("служба названа", строка.get("service"), "leadbot")

# --- то, ради чего журнал и заводился ---------------------------------------
chk("слой записан — это регламент или модель", строка.get("layer"), "локальный роутер")
chk("признак записан", строка.get("flag"), "price")
chk("уверенность записана", строка.get("confidence"), 1.0)
chk("время записано", строка.get("ms"), 42)
chk("заявка отмечена фактом", строка.get("lead_ready"), True)
chk("предупреждения дошли", строка.get("warnings"), ["воронка не увидит номер"])

# --- сквозной ключ на две системы -------------------------------------------
chk("request_id из LeadChat сохранён", строка.get("request_id"), "rq-from-leadchat")

# --- чего в журнале быть не должно ------------------------------------------
chk("телефона клиента нет", "+79161234567" in raw, False)
chk("имени из заявки нет", "Анна" in raw, False)
chk("текста ответа нет", "Ремонт холодильников" in raw, False)
chk("длина ответа есть вместо текста", строка.get("reply_len"), len(ОТВЕТ["reply"]))

# --- эскалация: причина и срок ----------------------------------------------
с_эскалацией = {
    "reply": "Передаю мастеру.",
    "confidence": 1.0,
    "needs_operator": True,
    "meta": {
        "layer": "локальный роутер",
        "request_id": "rq-2",
        "escalation": {"reason": "visit_failed", "label": "срыв визита", "deadline_min": 5},
    },
}
_, rows2 = записать(logbook.answered, с_эскалацией, source="leadchat", request_id="rq-2")
chk("причина эскалации записана", rows2[0].get("escalation_reason"), "visit_failed")
chk("срок реакции записан", rows2[0].get("escalation_deadline_min"), 5)

# --- «не смог» — отдельное событие, а не поле -------------------------------
_, rows3 = записать(logbook.refused, "таймаут 9 c", source="leadchat", request_id="rq-3", ms=9000)
chk("отказ — своё событие", rows3[0].get("event"), "leadbot.refused")
chk("причина отказа записана", rows3[0].get("reason"), "таймаут 9 c")

# --- сырой ответ движка: панель, тест-клиент, легаси-путь -------------------
#
# ЗАЧЕМ ОТДЕЛЬНОЕ СОБЫТИЕ. У сырого результата движка нет ни уверенности, ни
# `needs_operator` — они появляются только в контракте LeadChat. Зато есть
# `ready`, `missing` и `closed`, которых нет там. Свести их в одно событие
# значило бы либо выдумывать поля, либо терять.
СЫРОЙ = {
    "reply": "Назовите модель и район, посчитаю точнее.",
    "model": "локальный роутер",
    "flag": "price",
    "handoff": False,
    "ready": False,
    "missing": ["phone", "address"],
    "lead_ready": True,
    "lead": {"phone": "+79167654321"},
}
raw4, rows4 = записать(logbook.relayed, СЫРОЙ, source="panel", ms=31, dialog_len=4)
chk("сырой ответ — своё событие", rows4[0].get("event"), "leadbot.relayed")
chk("канал записан", rows4[0].get("source"), "panel")
chk("слой записан", rows4[0].get("layer"), "локальный роутер")
chk("чего не хватает — записано", rows4[0].get("missing"), ["phone", "address"])
chk("телефона нет и здесь", "+79167654321" in raw4, False)
chk("текста ответа нет и здесь", "Назовите модель" in raw4, False)

# --- журнал не имеет права уронить ответ ------------------------------------


class Неразбираемый:
    def __repr__(self):
        raise RuntimeError("и repr тоже падает")


try:
    записать(logbook.event, "leadbot.strange", value=Неразбираемый())
    упал = False
except Exception:
    упал = True
chk("несериализуемое поле не роняет журнал", упал, False)


def main():
    плохих = [(n, g, w) for n, g, w in CASES if g != w]
    for имя, получили, ждали in плохих:
        print("ПРОВАЛ: %s — получили %r, ждали %r" % (имя, получили, ждали))
    print("%d из %d" % (len(CASES) - len(плохих), len(CASES)))
    return 1 if плохих else 0


if __name__ == "__main__":
    sys.exit(main())
