# -*- coding: utf-8 -*-
"""Журнал решений лид-бота: по строке JSON на каждый ответ.

ЗАЧЕМ. Владелец: «сделай подробное логирование, чтобы было всё видно». До сих
пор бот писал в поток четырнадцать `print`-ов, и тринадцать из них — приветствие
при запуске. То есть на вопрос «почему он так ответил» ответить было нечем:
видно, что ответил, а какой слой сработал, какой роутер регламента, сколько это
стоило и почему ушло человеку — нет.

ПОЧЕМУ JSON, А НЕ ЧЕЛОВЕЧЕСКИЕ СТРОКИ. Служба живёт под systemd, вывод забирает
journald, а разбирают его потом `grep` и `jq`. У LeadChat формат ровно такой же
(`app/core/logging.py`), и это не совпадение: у одного обращения ОДИН
`request_id` на обе стороны — его придумывает LeadChat, мы возвращаем его в
`meta`. Значит один и тот же ключ находит запись и здесь, и там, и вопрос «где
потерялось» перестаёт быть археологией.

⚠ ЧЕГО ЗДЕСЬ НЕТ И НЕ ДОЛЖНО БЫТЬ. Текста клиента и текста ответа. Причина та
же, по которой их нет в журнале LeadChat: переписка уже лежит в чате, а её
копия в логах — это второе место, откуда она способна утечь, и второе, которое
надо чистить по сроку. Длину ответа пишем: по ней видно «промолчал» против
«ответил», а самого текста в ней нет.

ПОЧЕМУ НИЧЕГО НЕ БРОСАЕТ. Журнал — побочная польза. Упади он на сериализации
чужого объекта — и клиент не получит ответ ради нашей бухгалтерии. Здесь всё
завёрнуто, наружу не летит ничего.
"""

import json
import sys
import time

#: Печатать ли отладочные строки (`event=...debug`). Обычные решения пишутся
#: всегда: их немного — по одной на ответ, — и именно они отвечают на вопрос
#: «почему так». Подробности вроде промежуточных срабатываний роутеров нужны
#: редко и включаются переменной окружения на время разбора.
def _verbose():
    import os

    return (os.environ.get("LEADBOT_LOG_VERBOSE") or "").strip() not in ("", "0", "false")


def _write(payload):
    try:
        line = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        return
    try:
        # `print` с `flush`: под systemd вывод буферизуется, и без сброса
        # последние строки перед падением службы теряются — то есть ровно те,
        # ради которых журнал и читают.
        print(line, file=sys.stdout, flush=True)
    except Exception:
        pass


def где_упало(exc):
    """«(файл:строка, функция)» — последний кадр НАШЕГО кода в трассировке.

    ⚠ Без места разбор начинается с чтения всего конвейера: «TypeError: 'set' object
    is not subscriptable» ничего не говорит о том, в каком из сорока шагов это
    случилось (находка 18.08). Жило это внутри одного обработчика на мосту LeadChat,
    а в автоканале Avito отказ мозга уходил в строку панели вообще без места —
    поэтому помощник переехал сюда, к журналу, и им пользуются оба пути.
    """
    try:
        import traceback

        for кадр in reversed(traceback.extract_tb(exc.__traceback__)):
            if "/brain/" in кадр.filename:
                return " (%s:%d, %s)" % (кадр.filename.rsplit("/", 1)[-1],
                                         кадр.lineno, кадр.name)
    except Exception:
        pass
    return ""


def причина(exc):
    """Тип, текст и место одной строкой — то, что уходит в `refused`."""
    return "%s: %s%s" % (type(exc).__name__, exc, где_упало(exc))


def event(name, **fields):
    """Одна строка журнала. Не бросает ничего."""
    payload = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": name, "service": "leadbot"}
    payload.update(fields)
    _write(payload)


def debug(name, **fields):
    """То же, но только при включённом `LEADBOT_LOG_VERBOSE`."""
    if _verbose():
        event(name, debug=True, **fields)


def answered(answer, *, source, request_id="", ms=None, dialog_len=None):
    """Решение по одному обращению — главная строка этого журнала.

    Принимает ГОТОВЫЙ ответ (то, что уходит наружу), а не промежуточный
    результат: журнал обязан говорить о том, что увидел собеседник, а не о том,
    что мы про него думали. Расхождение этих двух вещей — самая дорогая порода
    ошибок, и ловится она только так.
    """
    answer = answer or {}
    meta = answer.get("meta") or {}
    escalation = meta.get("escalation") or {}
    reply = answer.get("reply") or ""

    fields = {
        # Один ключ на обе системы: его придумывает LeadChat и получает обратно
        # в `meta`. По нему же ищется запись в его журнале работы.
        "request_id": request_id or meta.get("request_id") or "",
        # Откуда пришло обращение: leadchat / jivo / panel. Без этого поля
        # нельзя отделить боевой поток от проверок и от старого канала.
        "source": source,
        # Кто ответил на самом деле: роутер регламента, кэш или модель. Главный
        # вопрос владельца к любому ответу — «это регламент или он сам придумал».
        "layer": meta.get("layer") or "",
        "flag": meta.get("flag"),
        "confidence": answer.get("confidence"),
        "needs_operator": bool(answer.get("needs_operator")),
        # Длина, а не текст: «промолчал» против «ответил» видно, переписки нет.
        "reply_len": len(reply),
        "ms": ms if ms is not None else meta.get("ms"),
    }
    if dialog_len is not None:
        fields["dialog_len"] = dialog_len
    if escalation:
        fields["escalation_reason"] = escalation.get("reason")
        fields["escalation_deadline_min"] = escalation.get("deadline_min")
    if meta.get("lead_ready"):
        # ⚠ ТОЛЬКО ФАКТ, НИКОГДА САМА ЗАЯВКА: в `meta.lead` лежит телефон
        # клиента, а журнал читают шире, чем переписку.
        fields["lead_ready"] = True
    if meta.get("missing"):
        fields["missing"] = meta["missing"]
    if meta.get("warnings"):
        fields["warnings"] = meta["warnings"]
    if meta.get("error"):
        fields["error"] = meta["error"]
    # ⚠ ПОЧЕМУ ПРОМОЛЧАЛИ (этап 5). «reply_len: 0» одинаково выглядит и у штатной
    # молчаливой передачи человеку (жалоба, чёрный список), и у отказа хода — модель
    # оборвалась на max_tokens и не вернула ответа вовсе. Различить их по журналу было
    # нельзя: причина лежала в `note` результата и до журнала не доезжала.
    if meta.get("reason"):
        fields["reason"] = meta["reason"]

    event("leadbot.answered", **fields)


def relayed(result, *, source, ms=None, dialog_len=None):
    """Ответ движка НЕ через контракт LeadChat: панель, тест-клиент, легаси-путь.

    ЗАЧЕМ ОТДЕЛЬНО ОТ `answered`. Там на входе готовый ответ по контракту
    LeadChat — с уверенностью и `needs_operator`, которых у сырого результата
    движка нет вовсе. Свести их в одну функцию значило бы либо выдумывать эти
    поля, либо терять те, что есть здесь (`ready`, `missing`, `closed`).
    Разные события и называются по-разному: в журнале сразу видно, каким путём
    пришло обращение, а это первый вопрос при разборе.
    """
    result = result or {}
    escalation = result.get("escalation") or {}
    reply = result.get("reply") or ""

    fields = {
        "source": source,
        "layer": result.get("model") or "",
        "flag": result.get("flag"),
        "handoff": bool(result.get("handoff") or result.get("to_operator")),
        "ready": bool(result.get("ready")),
        "reply_len": len(reply),
        "ms": ms,
    }
    if dialog_len is not None:
        fields["dialog_len"] = dialog_len
    if result.get("missing"):
        fields["missing"] = result["missing"]
    if escalation:
        fields["escalation_reason"] = escalation.get("reason")
        fields["escalation_deadline_min"] = escalation.get("deadline_min")
    if result.get("lead_ready"):
        # ⚠ Только факт: в `lead` лежит телефон клиента.
        fields["lead_ready"] = True
    if result.get("closed"):
        fields["closed"] = True
    if result.get("error"):
        fields["error"] = str(result["error"])[:300]

    event("leadbot.relayed", **fields)


def refused(reason, *, source, request_id="", ms=None):
    """Обращение, на которое ответить не смогли.

    Отдельным событием, а не полем в `answered`: «ответил» и «не смог» — разные
    вопросы, и складывать их в одно значит потерять тот, который важнее.
    """
    event(
        "leadbot.refused",
        request_id=request_id,
        source=source,
        reason=reason,
        ms=ms,
    )


def cost(model, usage, *, request_id="", source=""):
    """Что стоил вызов модели.

    Вызовы платные, и «почему в этом месяце дороже» — вопрос, на который до сих
    пор отвечал только `analysis/cost_log.jsonl`, лежащий на диске рядом с
    ботом. В журнале это та же цифра, но рядом с решением: видно не только
    сколько, но и на чём.
    """
    usage = usage or {}
    event(
        "leadbot.model_cost",
        request_id=request_id,
        source=source,
        model=model,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cache_read=usage.get("cache_read_input_tokens"),
    )
