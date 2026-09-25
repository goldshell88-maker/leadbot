# -*- coding: utf-8 -*-
"""Сопряжение с LeadChat: перевод форматов, доступ, идемпотентность, поведение под отказом.
Офлайн, 0 токенов к API — модель подменяется заглушкой. Запуск: python3 analysis/test_leadchat.py"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "brain"))
os.environ.setdefault("LEADBOT_SERVICE_TOKEN", "тест-токен-1234567890")
os.environ["LEADBOT_ANSWER_TIMEOUT_S"] = "1"

import leadchat                                   # noqa: E402
import server                                     # noqa: E402

CASES = []


def chk(name, got, want):
    CASES.append((name, got, want))


class H(dict):
    """Заголовки запроса: у BaseHTTPRequestHandler .get() без учёта регистра, нам хватает точных."""
    def get(self, k, d=None):
        return dict.get(self, k, d)


# ── доступ ───────────────────────────────────────────────────────────────────
TOK = os.environ["LEADBOT_SERVICE_TOKEN"]
chk("токен: Bearer", leadchat.token_ok(H({"Authorization": "Bearer " + TOK})), True)
chk("токен: свой заголовок", leadchat.token_ok(H({"X-Leadbot-Token": TOK})), True)
chk("токен: чужой", leadchat.token_ok(H({"Authorization": "Bearer нет"})), False)
chk("токен: пусто", leadchat.token_ok(H({})), False)
chk("токен: та же длина, другое значение",
    leadchat.token_ok(H({"X-Leadbot-Token": "X" * len(TOK)})), False)

# ── разбор запроса ───────────────────────────────────────────────────────────
BODY = {
    "request_id": "conv42:msg1007",
    "dialog": [{"role": "user", "content": "Холодильник не морозит"},
               {"role": "assistant", "content": "Здравствуйте, какая марка?"},
               {"role": "user", "content": "Атлант"}],
    "item_title": "Ремонт холодильников", "client_name": "Настя",
    "city": "Ульяновск", "persona": "Марк",
}
msgs, known, rid, warn = leadchat.parse_request(BODY)
chk("разбор: число реплик", len(msgs), 3)
chk("разбор: роли", [m["role"] for m in msgs], ["client", "bot", "client"])
chk("разбор: имя клиента", known["visitor"], "Настя")
chk("разбор: объявление", known["title"], "Ремонт холодильников")
chk("разбор: город", known["city"], "Ульяновск")
chk("разбор: персона", known["persona"], "Марк")
chk("разбор: request_id", rid, "conv42:msg1007")
chk("разбор: предупреждений нет", warn, [])

for bad, why in ((None, "не объект"), ({}, "нет dialog"), ({"dialog": []}, "пустой dialog"),
                 ({"dialog": [{"role": "assistant", "content": "привет"}]}, "нет клиента"),
                 ({"dialog": [{"role": "user", "content": "   "}]}, "только пробелы")):
    try:
        leadchat.parse_request(bad)
        chk("разбор-отказ: " + why, "прошло", "ValueError")
    except ValueError:
        chk("разбор-отказ: " + why, "ValueError", "ValueError")

# ⚠ главная ловушка стыковки: LeadChat маскирует телефоны перед отправкой в модель
_, _, _, warn2 = leadchat.parse_request(
    {"dialog": [{"role": "user", "content": "мой номер {PHONE}, звоните"}]})
chk("маскированный телефон замечен", bool(warn2), True)

# ── перевод результата ───────────────────────────────────────────────────────
a = leadchat.to_answer({"reply": "да, помогу. Какая марка?", "model": "claude-haiku-4-5"})
chk("модель: уверенность", a["confidence"], leadchat.CONF_MODEL)
chk("модель: оператор не нужен", a["needs_operator"], False)

a = leadchat.to_answer({"reply": "Здравствуйте\nЧто случилось?",
                        "model": "локальный роутер (мусор: greet_only)"})
chk("роутер: уверенность", a["confidence"], leadchat.CONF_ROUTER)

# «мостик + человеку»: текст УХОДИТ клиенту, затем движок делает handoff
a = leadchat.to_answer({"reply": "Гляну по графику и напишу", "handoff": True,
                        "model": "локальный роутер (уже наш клиент)"})
chk("мостик: текст сохранён", a["reply"], "Гляну по графику и напишу")
chk("мостик: нужен оператор", a["needs_operator"], True)
chk("мостик: уверенность выше порога 0.6", a["confidence"] > 0.6, True)

# «молча человеку» (жалоба): пустой текст + оператор
a = leadchat.to_answer({"reply": "", "to_operator": True, "handoff": True,
                        "model": "локальный роутер (жалоба, молча)"})
chk("молча: пустой текст", a["reply"], "")
chk("молча: нужен оператор", a["needs_operator"], True)
chk("молча: уверенность ноль", a["confidence"], leadchat.CONF_NONE)

u = leadchat.unavailable("таймаут", "r1", 9000)
chk("недоступность: оператор", u["needs_operator"], True)
chk("недоступность: пустой текст", u["reply"], "")

# ── идемпотентность ──────────────────────────────────────────────────────────
leadchat.reset_cache()
leadchat.remember("r-1", {"reply": "раз", "meta": {}})
again = leadchat.cached("r-1")
chk("повтор: тот же текст", again["reply"], "раз")
chk("повтор: помечен", again["meta"].get("repeat"), True)
chk("повтор: без id не кэшируем", leadchat.cached(""), None)

# ── сквозняк через эндпоинт (модель подменена) ───────────────────────────────
_real = server.run_relay
try:
    server.run_relay = lambda messages, known=None: {
        "reply": "да, помогу. Какая марка?", "model": "claude-haiku-4-5"}
    leadchat.reset_cache()
    out, code = server.leadchat_answer(BODY)
    chk("сквозняк: код", code, 200)
    chk("сквозняк: текст", out["reply"], "да, помогу. Какая марка?")
    chk("сквозняк: замер времени есть", isinstance(out["meta"]["ms"], int), True)
    out2, _ = server.leadchat_answer(BODY)
    chk("сквозняк: повтор из кэша", out2["meta"].get("repeat"), True)

    # падение мозга → не 500, а честное «нужен оператор»
    def _boom(messages, known=None):
        raise RuntimeError("шлюз лёг")
    server.run_relay = _boom
    leadchat.reset_cache()
    out, code = server.leadchat_answer(dict(BODY, request_id="r-err"))
    chk("падение: код 200", code, 200)
    chk("падение: нужен оператор", out["needs_operator"], True)
    chk("падение: причина видна", "шлюз лёг" in (out["meta"].get("reason") or ""), True)

    # таймаут: отвечаем РАНЬШЕ, чем движок LeadChat перестанет ждать
    def _slow(messages, known=None):
        time.sleep(5)
        return {"reply": "поздно"}
    server.run_relay = _slow
    leadchat.reset_cache()
    t0 = time.time()
    out, code = server.leadchat_answer(dict(BODY, request_id="r-slow"))
    took = time.time() - t0
    chk("таймаут: уложились в 3 с", took < 3.0, True)
    chk("таймаут: нужен оператор", out["needs_operator"], True)
    chk("таймаут: не кэшируется", leadchat.cached("r-slow"), None)

    # плохой запрос → 400 и всё равно валидный ответ для движка
    out, code = server.leadchat_answer({"dialog": []})
    chk("плохой запрос: код", code, 400)
    chk("плохой запрос: нужен оператор", out["needs_operator"], True)
finally:
    server.run_relay = _real

# ── причина эскалации, срок и «лид готов» доезжают до очереди LeadChat (12.08) ──
# Движку эти поля не нужны, очереди людей — обязательны: без причины её не отфильтровать,
# без срока не понять, что просрочено, а «лид готов» обязан идти ОТДЕЛЬНО от эскалаций.
_esc = leadchat.to_answer({"reply": "Уточню по времени и вернусь к вам", "handoff": True,
                           "model": "локальный роутер (эскалация: status)",
                           "escalation": {"reason": "status", "label": "вопрос о статусе визита",
                                          "deadline_min": 10, "note": "Вас ждать?"}})
chk("эскалация: причина доехала", _esc["meta"]["escalation"]["reason"], "status")
chk("эскалация: срок доехал", _esc["meta"]["escalation"]["deadline_min"], 10)
chk("эскалация: текст клиента доехал", _esc["meta"]["escalation"]["note"], "Вас ждать?")
chk("эскалация: не лид", _esc["meta"]["lead_ready"], False)

_lead = leadchat.to_answer({"reply": "могу к вам завтра к 14:00-14:30. Подтвержу ближе к делу",
                            "model": "claude-haiku-4-5",
                            "lead_ready": True, "lead": {"окно": "к 14", "телефон": True}})
chk("лид готов: признак", _lead["meta"]["lead_ready"], True)
chk("лид готов: окно видно", _lead["meta"]["lead"]["окно"], "к 14")
chk("лид готов: НЕ эскалация", _lead["meta"]["escalation"], None)
chk("лид готов: оператор не нужен", _lead["needs_operator"], False)

# сквозняк: боевой ход отдаёт оба поля
_out, _code = server.leadchat_answer(
    {"dialog": [{"role": "user", "content": "Вас ждать?"}], "request_id": "esc-1"})
chk("сквозняк: причина в meta", (_out["meta"].get("escalation") or {}).get("reason"), "status")
chk("сквозняк: срок в meta", (_out["meta"].get("escalation") or {}).get("deadline_min"), 10)

ok = 0
# ⚠ ЗАГЛУШКА ШЛЮЗА — ЧТОБЫ «0 ТОКЕНОВ» БЫЛО ПРАВДОЙ. Выше по файлу подменяется сама
# server.run_relay, но проверкам ниже нужен НАСТОЯЩИЙ ход целиком, со всеми гардами.
# Форма — СЫРОЙ ответ Anthropic: плоский словарь сервер честно не разбирает.
import claude_api as _ca                          # noqa: E402


def _stub_reply(текст):
    _база = {"reply": текст, "city": "", "phone": "", "address": "", "service": "",
             "preferred_time": "", "category": "kp", "ready_to_create": False,
             "handoff_to_human": False, "pass_to_operator": False}
    _ca.messages = lambda *a, **k: {
        "content": [{"type": "tool_use", "name": "dispatcher_turn", "input": dict(_база)}],
        "usage": {}, "model": "offline-stub"}


_stub_reply("да, посмотрю")
# после блока «сквозняк» run_relay мог остаться подменённым — возвращаем настоящий
server.run_relay = _real

# ── ДВА НОВЫХ НЕОБЯЗАТЕЛЬНЫХ ПОЛЯ КОНТРАКТА (24.08) ─────────────────────────
# `prior_suggestions` — мои прошлые подсказки. В режиме подсказки они лежат заметками,
# а история для бота собирается только из 'in'/'out': своих слов бот не видел вовсе,
# и все анти-повторные гарды считали по репликам ОПЕРАТОРА. Отсюда три просьбы номера
# при правиле «максимум две» и одна фраза дословно три хода подряд.
# `events` — системные записи, прежде всего звонки: клиент набирал номер трижды, а чат
# в это время просил у него телефон.
_ДИАЛОГ = [{"role": "user", "content": "Неисправность головки, Canon G2470"},
           {"role": "assistant", "content": "Понял вас"},
           {"role": "user", "content": "что делать будем?"}]
_БАЗА = {"dialog": _ДИАЛОГ, "city": "Москва", "direction": "kp"}

_m, _k, _r, _w = leadchat.parse_request(dict(_БАЗА))
chk("без новых полей: свои подсказки пусты", _k["prior_self"], [])
chk("без новых полей: звонков ноль", _k["calls"], 0)
chk("без новых полей: история разобрана целиком", len(_m), 3)

_m, _k, _r, _w = leadchat.parse_request(dict(
    _БАЗА, prior_suggestions=["И номер ваш подскажите", "И номер ваш подскажите, пожалуйста"],
    events=[{"type": "call", "at": "2026-08-24T17:48:00Z"},
            {"type": "call", "at": "2026-08-24T20:32:00Z"},
            {"type": "read", "at": "2026-08-24T20:33:00Z"}]))
chk("свои подсказки прочитаны", len(_k["prior_self"]), 2)
chk("звонки посчитаны, прочее не считается", _k["calls"], 2)
chk("⚠ свои подсказки НЕ попадают в историю диалога", len(_m), 3)

# ⚠ ПОЧЕМУ ОТДЕЛЬНЫМ ПОЛЕМ, А НЕ В ИСТОРИИ. Клиент подсказок не видел: оператор мог их
# проигнорировать и написать своё. Подмешать их в messages значит убедить бота, что он
# уже поздоровался, и срезать приветствие, которого клиент не получал.
chk("история осталась только клиент+оператор",
    sorted({m["role"] for m in _m}), ["bot", "client"])

# ── ПОВЕДЕНИЕ: третьей просьбы номера не бывает ─────────────────────────────
def _ответ(known_extra):
    _stub_reply("да, посмотрю ваш принтер, это обычная работа")
    try:
        server._ANSWER_CACHE.clear()
    except Exception:
        pass
    _msgs = [{"role": "client", "text": "Неисправность печатающей головки, Canon G2470"},
             {"role": "operator", "text": "Понял вас"},
             {"role": "client", "text": "что делать будем?"}]
    _kn = dict({"city": "Москва", "direction": "kp"}, **known_extra)
    return (server.run_relay(_msgs, known=_kn).get("reply_text") or "")


chk("номер добирается, когда его ещё не просили",
    "номер" in _ответ({}).lower(), True)
chk("номер НЕ добирается, если я уже просил дважды",
    "номер" in _ответ({"prior_self": ["Понял. И номер ваш подскажите",
                                      "И номер ваш подскажите, пожалуйста"]}).lower(), False)
chk("номер НЕ просим у того, кто звонит",
    "номер" in _ответ({"calls": 1}).lower(), False)

# ── ПОВЕДЕНИЕ: два звонка подряд — это просьба перезвонить ───────────────────
_r2 = server.run_relay(
    [{"role": "client", "text": "Неисправность головки, Canon G2470"}],
    known={"city": "Москва", "direction": "kp", "calls": 3})
chk("три звонка: зовём человека", _r2["notify_human"], True)
# ⚠ ПРИЧИНА ПЕРЕИМЕНОВАНА 26.08: «звонил» и «просит позвонить» — разные события.
# Снимок владельца: клиент НАБРАЛ номер через приложение Авито, а в очереди висело
# «клиент просит позвонить» — при том что в переписке он спрашивал, приедем ли завтра.
# Оператор читает ярлык и идёт звонить вместо того, чтобы ответить по делу.
chk("три звонка: причина — звонок", (_r2.get("escalation") or {}).get("reason"), "call_made")
chk("три звонка: срок 15 минут", (_r2.get("escalation") or {}).get("deadline_min"), 15)
chk("три звонка: номер не просим", "номер" in (_r2.get("reply_text") or "").lower(), False)
chk("три звонка: звонок от бота не обещаем",
    any(w in (_r2.get("reply_text") or "").lower() for w in ("перезвоню", "наберу вас")), False)

# ⚠ ЭТА ПРОВЕРКА ТРЕБОВАЛА ОБРАТНОГО — «один звонок эскалацией не считается: клиент мог
# не дозвониться и просто написать». Владелец отменил правило 25.08, разобрав живой диалог
# с клиентом из Губкина. Довод дословно: «мы не знаем, сколько он говорил и дозвонился бы
# он вообще — это сможет узнать только оператор». Бот после звонка отвечает вслепую: он не
# слышал разговора и не знает, договорились ли уже. В том диалоге он на один звонок написал
# «Сразу сориентирую по мастеру и времени» — обещание за людей, которых не назначал.
# Цена решения замерена: ровно один звонок в 449 диалогах из 30 526 (1,5 %) — столько ходов
# уходит человеку дополнительно.
# ⚠⚠ 26.08 ВЛАДЕЛЕЦ ОТМЕНИЛ И ЭТО РЕШЕНИЕ, разобрав снимок из Нефтеюганска. Дословно:
# «Из практики люди могут позвонить и не дозвониться, поэтому мы можем спокойно такое
# отработать и создать заявку. Если будет явно видно по диалогу и контексту, что клиент
# звонил, то дальше лучше не отвечать и передать такой диалог». На том снимке клиент один
# раз звонил и писал в чат живой рабочий вопрос «Завтра вечером сможете приехать
# посмотреть?» — бот промолчал и отдал диалог человеку, потеряв темп.
# Теперь молчим при ДВУХ вызовах либо при одном, но со следом разговора в переписке.
#
# ⚠ ПРОВЕРКА ИДЁТ ЧЕРЕЗ `run_relay` ТОЛЬКО ТАМ, ГДЕ РОУТЕР ЗАМЫКАЕТ ХОД ДО МОДЕЛИ.
# Случай «один звонок без следа разговора» модель как раз ОБРАБАТЫВАЕТ, то есть полный
# прогон здесь стоил бы токенов и сделал бы набор платным (проверено на себе 26.08:
# первая редакция этой правки увела набор в шлюз и сожгла два вызова). Его проверяем
# на условии молчания, а не на всей цепочке.
_r1 = server.run_relay(
    [{"role": "client", "text": "Неисправность головки, Canon G2470"}],
    known={"city": "Москва", "direction": "kp", "calls": 2})
chk("два звонка: зовём человека", _r1["notify_human"], True)
chk("два звонка: причина — звонок",
    (_r1.get("escalation") or {}).get("reason"), "call_made")
chk("два звонка: номер не просим", "номер" in (_r1.get("reply_text") or "").lower(), False)

_r1b = server.run_relay(
    [{"role": "client", "text": "Мы с вами только что созванивались, приедете сегодня?"}],
    known={"city": "Москва", "direction": "kp", "calls": 1})
chk("один звонок + след разговора: зовём человека", _r1b["notify_human"], True)
chk("один звонок + след разговора: причина — звонок",
    (_r1b.get("escalation") or {}).get("reason"), "call_made")

chk("один звонок без следа разговора бота НЕ глушит",
    bool(server._РАЗГОВОР_БЫЛ_RX.search("Завтра вечером сможете приехать посмотреть?")), False)
chk("«не дозвонился» — это НЕ состоявшийся разговор",
    bool(server._РАЗГОВОР_БЫЛ_RX.search("я вам звонил, не дозвонился")), False)
chk("«созвонились» — состоявшийся разговор",
    bool(server._РАЗГОВОР_БЫЛ_RX.search("мы с вами созвонились")), True)

for name, got, want in CASES:
    if got == want:
        ok += 1
    else:
        print("  ПРОВАЛ: %-42s ждали=%r получили=%r" % (name, want, got))
print("=" * 55)
print("LeadChat-сопряжение: %d/%d" % (ok, len(CASES)))
sys.exit(0 if ok == len(CASES) else 1)
