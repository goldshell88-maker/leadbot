# -*- coding: utf-8 -*-
"""Сопряжение с LeadChat: бот как поставщик ответов для шага `ai_answer`.

ЗАЧЕМ ОТДЕЛЬНЫЙ СЛОЙ. Движок сценариев LeadChat (`app/bots/engine.py`) зовёт AI через
маленький протокол — `ai_answer(bot, dialog, item_title) -> {reply, confidence,
needs_operator} | None`. Это и есть шов, в который встаёт наш мозг: LeadChat перестаёт
ходить в модель сам и спрашивает нас, а мы отвечаем уже по регламенту, с роутерами,
RAG и всеми гардами. Здесь — только перевод форматов туда и обратно, без сетевой части
(её держит server.py) и без бизнес-логики (она в prefilter/prompt/server).

КАК ЧИТАЕТ РЕЗУЛЬТАТ ДВИЖОК LeadChat (проверено по его коду, engine.exec_ai_answer):
  • `None`/ошибка/таймаут → заметка «AI недоступен» и handoff. Ретраев НЕТ ни одного.
  • `needs_operator=true` и текст непустой → текст УХОДИТ клиенту как фраза-мост,
    затем handoff. Это ровно наша схема «мостик + человеку».
  • `confidence < confidence_threshold` (по умолчанию 0.6) → handoff, причём текст
    клиенту НЕ уходит. Поэтому занижать уверенность у хорошего ответа нельзя.
  • иначе → текст уходит клиенту, сценарий идёт дальше.
Вывод: наш «молча оператору» = пустой reply + needs_operator; наш «мостик» = текст +
needs_operator; обычный ответ = текст + высокая уверенность.

ЧТО ЕЩЁ ЛЕЖИТ В meta (движку не нужно, очереди — обязательно):
  meta.escalation = {reason, label, deadline_min} — почему диалог отдан человеку и за сколько
      минут на него надо ответить. Срок разный по причинам: срыв визита 5, статус 10,
      претензия 15, перенос 30, отмена и согласование 120.
  meta.lead_ready + meta.lead = заявка собрана (телефон есть, окно предложено). Это НЕ
      эскалация: такие диалоги идут в свою очередь на подтверждение времени человеком,
      потому что календаря у бота нет и слот ничем не подтверждён.
"""

import json
import os
import re
import time

# Уверенность по слою, который дал ответ. Роутеры детерминированы — им 1.0; ответ модели
# прошёл через все гарды, но остаётся вероятностным — 0.9. Обе величины ВЫШЕ порога 0.6,
# иначе движок выбросит готовый ответ и уйдёт в handoff.
CONF_ROUTER = 1.0
CONF_MODEL = 0.9
CONF_NONE = 0.0

# Наш потолок ответа. У LeadChat таймаут шага 10 секунд (AI_TIMEOUT_SECONDS), и по его
# истечении диалог уходит в handoff без единого ретрая. Отвечаем заведомо раньше, чтобы
# решение о передаче человеку принимали МЫ и осмысленно, а не их секундомер.
DEFAULT_TIMEOUT_S = 9.0

# Идемпотентность: LeadChat не ретраит шаг, но сеть и балансировщик могут задвоить запрос,
# а каждый лишний вызов — это деньги и второй ответ клиенту.
_IDEMPOTENCY_TTL_S = 600
_IDEMPOTENCY_MAX = 500
_seen = {}          # request_id -> (когда, ответ)

_MASKED_PHONE_RX = re.compile(r"\{PHONE\}")
_ROLE_CLIENT = "user"


def service_token():
    """Токен для вызовов сервер-сервер. Пусто — эндпоинт закрыт наглухо."""
    return (os.environ.get("LEADBOT_SERVICE_TOKEN") or "").strip()


def timeout_s():
    try:
        v = float(os.environ.get("LEADBOT_ANSWER_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S
    return v if 1.0 <= v <= 60.0 else DEFAULT_TIMEOUT_S


def token_ok(headers):
    """Bearer или X-Leadbot-Token. Сравнение без раннего выхода по длине."""
    want = service_token()
    if not want:
        return False
    got = ""
    auth = (headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        got = auth[7:].strip()
    if not got:
        got = (headers.get("X-Leadbot-Token") or "").strip()
    if not got or len(got) != len(want):
        return False
    diff = 0
    for a, b in zip(got, want):
        diff |= ord(a) ^ ord(b)
    return diff == 0


def _текст_реплики(content):
    """Текст одной реплики из чего угодно, что прислал движок.

    ⚠ БЛОЧНЫЙ КОНТЕНТ РОНЯЛ ВЕСЬ ХОД (этап 5). Здесь стояло
    `(item.get("content") or "").strip()`, а формат сообщений у моделей давно
    допускает список блоков (`[{"type":"text","text":"…"}]`) — ровно так их держит
    у себя и LeadChat. На таком запросе летел AttributeError, и летел он ДО
    защитного `except ValueError` в server.leadchat_answer: наружу не уходило
    ничего — ни ответа, ни кода, ни строки журнала. Клиент ждал десять секунд
    чужого секундомера, а мы даже не знали, что нас спрашивали.
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        куски = []
        for блок in content:
            if isinstance(блок, str):
                куски.append(блок)
            elif isinstance(блок, dict) and isinstance(блок.get("text"), str):
                куски.append(блок["text"])
        return "\n".join(к for к in куски if к.strip()).strip()
    return ""


def parse_request(body):
    """Payload LeadChat → (messages для run_relay, known, request_id, предупреждения).

    Бросает ValueError с человекочитаемым текстом, если диалога нет — движку это
    вернётся как ошибка, и он честно уйдёт в handoff."""
    if not isinstance(body, dict):
        raise ValueError("тело запроса не объект")
    dialog = body.get("dialog")
    if not isinstance(dialog, list) or not dialog:
        raise ValueError("пустой dialog")

    messages, warn = [], []
    for item in dialog:
        if not isinstance(item, dict):
            continue
        text = _текст_реплики(item.get("content"))
        if not text:
            text = _текст_реплики(item.get("text"))
        if not text:
            continue
        role = "client" if (item.get("role") or "") == _ROLE_CLIENT else "bot"
        # ⚠ ЧЬЯ ЭТО РЕПЛИКА — НАША ИЛИ ЖИВОГО ОПЕРАТОРА. Роль этого не различает: панель
        # шлёт `assistant` и на свой ответ бота, и на сообщение человека, а заслон
        # «диалог уже ведёт человек» считает именно операторские ходы. Без разделения бот
        # глушил САМ СЕБЯ на третьей своей реплике — ровно там, где заявка уже собрана
        # (разбор снимка 26.08: 517 диалогов за неделю, в 516 из них человек не писал вовсе).
        # Поле необязательное: панель старой сборки его не шлёт, и тогда всё как раньше.
        свой = str(item.get("by") or "").strip().lower() == "bot"
        # ⚠ ПОДРЯД-ДУБЛЬ РЕПЛИКИ КЛИЕНТА СХЛОПЫВАЕТСЯ (30.08, хвост №1 аудита):
        # задвоенный вебхук Авито давал «текст, текст» — модель видела эхо и
        # отвечала на одну реплику по нескольку раз разными словами.
        if (messages and role == "client" and messages[-1]["role"] == "client"
                and messages[-1]["text"].strip() == text.strip()):
            continue
        messages.append({"role": role, "text": text, "свой": свой})
    # ⚠ ДИАГНОСТИКА ПОТЕРИ КОНТЕКСТА (19.08). В 21 вызове из 148 за день бот получал
    # 1-3 реплики там, где в диалоге LeadChat лежало 10-19 — и отвечал вслепую:
    # здоровался посреди разговора, переспрашивал адрес, который уже назвали.
    # Кто теряет — не видно: код LeadChat читает 30 сообщений, а до нас доходит одна.
    # Эта запись разделяет два случая: «прислали мало» и «наш парсер выбросил».
    if len(dialog) != len(messages):
        warn.append("из %d реплик payload разобрано %d — остальные без текста или "
                    "не словарь" % (len(dialog), len(messages)))
    if not messages:
        raise ValueError("в dialog нет непустых реплик")
    if not any(m["role"] == "client" for m in messages):
        raise ValueError("в dialog нет ни одной реплики клиента")

    # ⚠ LeadChat маскирует телефоны перед отправкой в модель (у него запрос уходит за
    # границу через прокси). НАМ маскировать нечего и незачем: мы свой сервис в том же
    # контуре, а без живого номера воронка не увидит, что телефон уже получен, и будет
    # просить его снова и снова. Адаптер на стороне LeadChat обязан слать сырой текст.
    if any(_MASKED_PHONE_RX.search(m["text"]) for m in messages):
        warn.append("телефоны замаскированы {PHONE}: воронка не увидит номер и будет "
                    "просить его повторно — шлите сырой текст")

    # ⚠ ДВА ПОЛЯ СВЕРХ ПРЕЖНЕГО КОНТРАКТА (24.08). Оба НЕОБЯЗАТЕЛЬНЫЕ: панель, которая их
    # не шлёт, работает ровно как раньше — поэтому выкатывать можно в любом порядке, а не
    # «строго бот, потом панель».
    #
    # `prior_suggestions` — МОИ прошлые подсказки. В режиме подсказки они лежат ЗАМЕТКАМИ
    # (direction='note'), а историю для меня панель собирает только из 'in'/'out' — то есть
    # своих слов я не вижу вовсе. Замер на двадцати гардах одного диалога: тринадцать меняют
    # поведение, стоит добавить их в историю. Отсюда тройная просьба номера при правиле
    # «максимум две» и дословный повтор фразы трижды подряд.
    # ⚠ ЭТО НЕ ИСТОРИЯ ДИАЛОГА. Клиент подсказок НЕ ВИДЕЛ: оператор мог их проигнорировать
    # и написать своё. Поэтому поле кладётся ОТДЕЛЬНО, а не подмешивается в messages: иначе
    # бот решит, что уже поздоровался, и срежет приветствие, которого клиент не получал.
    _свои = [_текст_реплики(x) for x in (body.get("prior_suggestions") or [])
             if isinstance(x, (str, dict, list))]
    # `events` — системные записи диалога, прежде всего ЗВОНКИ. Клиент, который трижды
    # набирает номер, а в чате получает просьбу назвать телефон, — живой случай 23.08.
    _звонков = 0
    for e in (body.get("events") or []):
        if isinstance(e, dict) and (e.get("type") or "") == "call":
            _звонков += 1
    known = {
        "prior_self": [x for x in _свои if x][-12:],
        "calls": _звонков,
        "visitor": (body.get("client_name") or "").strip(),
        "city": (body.get("city") or "").strip(),
        "title": (body.get("item_title") or "").strip(),
        "direction": (body.get("direction") or "").strip(),
        "persona": (body.get("persona") or "").strip(),
        "partner": (body.get("partner") or "").strip(),
        # стабильный маркер диалога для теневого журнала (хвост №7 аудита 30.08)
        "chat_id": (body.get("chat") or "").strip(),
        # канальные данные для заявки (16.08): источник «В95» — в комментарий
        # партнёра, ссылка отзыва — хвостом основного комментария в 🟢
        "origin": (body.get("origin") or "").strip(),
        "review_url": (body.get("review_url") or "").strip(),
        "white": bool(body.get("white")),
    }
    rid = (body.get("request_id") or "").strip()[:200]
    return messages, known, rid, warn


def to_answer(result, warnings=(), ms=None, request_id=""):
    """Результат run_relay → ответ в формате шага `ai_answer` LeadChat."""
    result = result or {}
    reply = (result.get("reply") or "").strip()
    layer = result.get("model") or ""
    # ⚠ ПУСТОЙ ОТВЕТ ПОЧТИ ВСЕГДА ЗНАЧИТ «ЗОВИТЕ ЧЕЛОВЕКА» — И ОДИН РАЗ НЕ ЗНАЧИТ.
    # Условие `not reply` стоит здесь правильно: обрыв ответа модели обязан поднять человека.
    # Но на вежливое «Ок» бот молчит ОСОЗНАННО и должен остаться в диалоге: панель второй раз
    # его не пустит (`bot_entry_block`: handoff_done), и заявка, которую он собрал бы позже,
    # пропадёт вместе с диалогом. Мозг помечает такой случай флагом `silent_hold` — только он
    # и снимает автоматическое «нужен оператор» (снимок владельца 27.08).
    _тихо = str(((result.get("flag") or {}) if isinstance(result.get("flag"), dict) else {})
                .get("kind") or "") == "silent_hold"
    needs_operator = bool(result.get("handoff") or result.get("to_operator")
                          or (not reply and not _тихо))

    if _тихо:
        # решение детерминированное, роутерное — и порог уверенности его гасить не должен,
        # иначе панель сама позовёт человека тем же путём, который мы только что закрыли
        conf = CONF_ROUTER
    elif not reply:
        conf = CONF_NONE
    elif layer.startswith(("локальн", "кэш")):
        conf = CONF_ROUTER
    else:
        conf = CONF_MODEL

    # ⚠ 12.08: контракт бота с тех пор развёлся — появились явная эскалация с причиной и
    # сроком реакции и отдельное событие «лид готов». Их нужно отдавать наружу: без причины
    # очередь LeadChat не отфильтровать, без срока — не понять, что просрочено, а «лид готов»
    # обязан идти ОТДЕЛЬНО от эскалаций, иначе очередь претензий утонет в успешных заявках.
    esc = result.get("escalation") or None
    meta = {"layer": layer, "flag": result.get("flag"),
            "missing": result.get("missing"), "ready": bool(result.get("ready")),
            "escalation": esc, "lead_ready": bool(result.get("lead_ready")),
            # ⚠ «КЛИЕНТ ЗАКРЫЛ РАЗГОВОР» — ОТДЕЛЬНЫМ ПОЛЕМ (боевой диалог 29.08,
            # Пятигорск: «мне выезд не подходит» → бот попрощался, а диалог в панели
            # остался живым). LeadChat по нему закрывает диалог с итогом «отказался».
            "client_closed": bool(result.get("client_closed")),
            "lead": result.get("lead"),
            "ms": ms, "request_id": request_id}
    if warnings:
        meta["warnings"] = list(warnings)
    if result.get("error"):
        meta["error"] = str(result["error"])[:300]
    # ⚠ ПРИЧИНА МОЛЧАНИЯ ЕДЕТ НАРУЖУ (этап 5). Пустой текст бывает штатным («жалоба —
    # молча человеку») и аварийным («модель не вернула ответ, передаю оператору»), а в
    # журнале и там и там стояла одна строка «ответил длиной 0». Объяснение уже готово
    # в `note` результата — до сих пор его просто никто не передавал дальше.
    # ⚠ 30.08: причина пишется и при НЕПУСТОМ reply — квитанция за номер (решение
    # по хвосту №2 аудита) сделала ответ ненулевым, и «почему молчала модель»
    # пропадало из журнала, хотя нота есть.
    if result.get("note"):
        meta["reason"] = str(result["note"])[:300]
    return {"reply": reply, "confidence": conf,
            "needs_operator": needs_operator, "meta": meta}


def unavailable(reason, request_id="", ms=None):
    """Ответ, при котором движок LeadChat гарантированно отдаст диалог человеку.

    Пустой текст + needs_operator: клиент не получит ни отписки, ни выдуманного ответа,
    оператор подхватит. Именно так надо отвечать на таймаут и на внутреннюю ошибку —
    молчание в чате хуже, чем живой человек через минуту."""
    return {"reply": "", "confidence": CONF_NONE, "needs_operator": True,
            "meta": {"layer": "недоступен", "reason": reason,
                     "request_id": request_id, "ms": ms}}


# ---- идемпотентность --------------------------------------------------------

def cached(request_id):
    if not request_id:
        return None
    rec = _seen.get(request_id)
    if not rec:
        return None
    when, payload = rec
    if time.time() - when > _IDEMPOTENCY_TTL_S:
        _seen.pop(request_id, None)
        return None
    out = json.loads(json.dumps(payload))       # копия: вызывающий дописывает meta
    out.setdefault("meta", {})["repeat"] = True
    return out


def remember(request_id, payload):
    if not request_id:
        return
    if len(_seen) >= _IDEMPOTENCY_MAX:
        for k, _ in sorted(_seen.items(), key=lambda kv: kv[1][0])[:_IDEMPOTENCY_MAX // 4]:
            _seen.pop(k, None)
    _seen[request_id] = (time.time(), payload)


def reset_cache():
    _seen.clear()
