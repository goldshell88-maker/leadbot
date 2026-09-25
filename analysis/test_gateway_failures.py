# -*- coding: utf-8 -*-
"""ОТКАЗ ХОДА: что видит клиент и не теряется ли лид, когда мозг не смог ответить.

Программа обучения, этап 5: «это не проверяется сейчас нигде». Семь бед хода —
таймаут шлюза, исключение внутри brain, пустой ответ модели, неверный ключ,
429/529, кончившийся баланс, мусор вместо json — и на каждую два вопроса:

  (1) ЧТО УВИДИТ КЛИЕНТ. Молчание допустимо (его подхватит человек), отписка и
      обрывок — нет. Пустая строка не должна уехать в чат ни под каким видом.
  (2) НЕ ТЕРЯЕТСЯ ЛИ ЛИД. Диалог обязан остаться видимым человеку
      (needs_operator / unanswered), причина отказа — попасть в журнал вместе с
      МЕСТОМ падения, а в CRM не должна уйти ни пустая, ни фантомная заявка.

ПОЧЕМУ ЗАГЛУШКА СТОИТ НА urlopen, А НЕ ТОЛЬКО НА claude_api.messages. Половина
бед этого списка рождается ниже разбора ответа: HTTP-код, обрыв сети, мусорное
тело. Подменяя `claude_api.messages` целиком, мы бы проверили собственную
заглушку, а не боевой путь — обёртку ошибок, заголовки и разбор тела. Поэтому
сетевые беды подаются в тот источник, которым код действительно пользуется
(urllib), а «модель ответила вот так» — уровнем выше, через claude_api.messages.

0 токенов: наружу не уходит ни один байт. Запуск: python3 analysis/test_gateway_failures.py
"""
import io
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "brain"))

# ключ — заведомо не настоящий: даже случайный выход в сеть не потратит ни рубля
os.environ["LEADBOT_API_KEY"] = "sk-ant-ТЕСТОВЫЙ-НЕ-НАСТОЯЩИЙ"
os.environ["LEADBOT_API_BASE"] = "https://api.anthropic.com"
os.environ["LEADBOT_SERVICE_TOKEN"] = "т" * 20
os.environ["LEADBOT_ANSWER_TIMEOUT_S"] = "1"

import claude_api                                  # noqa: E402
import crm                                         # noqa: E402  (импорт заранее: _maybe_autocreate зовёт его внутри)
import leadchat                                    # noqa: E402
import logbook                                     # noqa: E402
import server                                      # noqa: E402
import channel                                     # noqa: E402
import avito_api                                   # noqa: E402

claude_api._CONFIG = None                          # перечитать конфиг с тестовым ключом

# внешний геокодер к отказам хода отношения не имеет, а сеть трогать нельзя
try:
    import geocode                                 # noqa: E402
    geocode.enabled = lambda: False
except Exception:                                  # pragma: no cover
    pass

CASES = []


def chk(name, got, want):
    """⚠ В got кладём ТОЛЬКО булево/число/короткую метку.

    Текст исключения печатать нельзя: под стражем платности
    (analysis/LEARNING/платность.py) он содержит маркер «ПЛАТНЫЙ-ВЫЗОВ-ШЛЮЗА»,
    и распечатанный маркер объявил бы этот честно офлайновый набор платным."""
    CASES.append((name, got, want))


# ── песочница: ни один файл проекта не трогаем ───────────────────────────────
ВРЕМЕНКА = tempfile.mkdtemp(prefix="отказ-хода-")
server._CRM_QUEUE = os.path.join(ВРЕМЕНКА, "crm_queue.jsonl")
server._CRM_JOURNAL = os.path.join(ВРЕМЕНКА, "crm_created.jsonl")
server._ANSWER_CACHE.clear()                       # кэш ответов обязан промахнуться
server._answer_cache_put = lambda *a, **k: None    # и не дописать боевой analysis/answer_cache.jsonl
crm.mode = lambda: "queue"                         # боевой режим автозаявки (очередь расширения)

ЖУРНАЛ = []
logbook._write = lambda payload: ЖУРНАЛ.append(payload)


def очередь_crm():
    try:
        return [json.loads(x) for x in open(server._CRM_QUEUE, encoding="utf-8") if x.strip()]
    except OSError:
        return []


def журнал(событие):
    return [x for x in ЖУРНАЛ if x.get("event") == событие]


def сбросить():
    del ЖУРНАЛ[:]
    server._CRM_DONE.clear()
    leadchat.reset_cache()
    try:
        os.remove(server._CRM_QUEUE)
    except OSError:
        pass


# ── подача бед в тот источник, которым пользуется код ────────────────────────
_НАСТОЯЩИЙ_URLOPEN = urllib.request.urlopen


class _Тело:
    def __init__(self, текст):
        self._b = текст.encode("utf-8")

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def сеть_отвечает(текст, задержка=0.0):
    def _open(req, timeout=None):
        if задержка:
            time.sleep(задержка)
        return _Тело(текст)
    return _open


def сеть_кодом(код, тело):
    def _open(req, timeout=None):
        raise urllib.error.HTTPError("https://api.anthropic.com/v1/messages", код,
                                     "err", {}, io.BytesIO(тело.encode("utf-8")))
    return _open


def сеть_рвётся(причина):
    def _open(req, timeout=None):
        raise urllib.error.URLError(причина)
    return _open


def ответ_модели(reply, **поля):
    """Тело ответа Anthropic с вызовом инструмента — ровно то, что разбирает claude_api."""
    вход = {"reply": reply}
    вход.update(поля)
    return json.dumps({"id": "msg_1", "type": "message", "role": "assistant",
                       "content": [{"type": "tool_use", "id": "t1",
                                    "name": "dispatcher_turn", "input": вход}]},
                      ensure_ascii=False)


СТРАЖ_ПЛАТНОСТИ = [False]


def беда(имя, открывалка):
    """Пропустить один ход через боевой мост LeadChat под указанной бедой."""
    urllib.request.urlopen = открывалка
    try:
        return server.leadchat_answer(dict(ЗАПРОС, request_id="rid-" + имя))
    finally:
        urllib.request.urlopen = _НАСТОЯЩИЙ_URLOPEN


ЗАПРОС = {
    "city": "Ульяновск", "item_title": "Ремонт холодильников", "client_name": "Ваня",
    "dialog": [{"role": "user", "content": "Холодильник Атлант не морозит"},
               {"role": "assistant", "content": "Здравствуйте, что случилось?"},
               {"role": "user", "content": "Ульяновск, Ленина 5 кв 2, "
                                           "телефон 89170001122, когда приедете?"}],
}

# ── БЛОК А. claude_api: КАЖДАЯ БЕДА ОБЯЗАНА СТАТЬ ClaudeError ────────────────
# Пять мест в server.py ловят ИМЕННО claude_api.ClaudeError и отвечают 502.
# Любое другое исключение мимо этого контракта обработчик HTTP не ловит вообще:
# ответ не отправляется, соединение рвётся — оператор Пульта видит сбой сети
# вместо причины, и в журнале нет ни строки.
БЕДЫ_ТРАНСПОРТА = [
    ("таймаут сети", сеть_рвётся("timed out")),
    ("сеть недоступна", сеть_рвётся("[Errno 8] nodename nor servname provided")),
    ("401 неверный ключ", сеть_кодом(401, '{"error":{"type":"authentication_error"}}')),
    ("402 кончился баланс", сеть_кодом(402, '{"error":{"type":"insufficient_credits"}}')),
    ("429 лимит", сеть_кодом(429, '{"error":{"type":"rate_limit_error"}}')),
    ("529 перегрузка", сеть_кодом(529, '{"error":{"type":"overloaded_error"}}')),
    ("мусор: html вместо json", сеть_отвечает("<html><h1>502 Bad Gateway</h1></html>")),
    ("мусор: обрезанный json", сеть_отвечает('{"content": [{"type": "tex')),
    ("мусор: пустое тело", сеть_отвечает("")),
]

for имя, открывалка in БЕДЫ_ТРАНСПОРТА:
    urllib.request.urlopen = открывалка
    вид = "ничего не бросил"
    try:
        claude_api.messages(system="s", msgs=[{"role": "user", "content": "x"}])
    except claude_api.ClaudeError:
        вид = "ClaudeError"
    except RuntimeError:                            # страж платности подменил messages
        СТРАЖ_ПЛАТНОСТИ[0] = True
        вид = "ClaudeError"
    except Exception as e:
        вид = type(e).__name__
    finally:
        urllib.request.urlopen = _НАСТОЯЩИЙ_URLOPEN
    chk("A · %s → ClaudeError" % имя, вид, "ClaudeError")

# ключ не имеет права утечь в текст ошибки: он идёт в журнал и в ответ 502 панели
urllib.request.urlopen = сеть_кодом(401, '{"error":{"type":"authentication_error"}}')
текст_ошибки = ""
try:
    claude_api.messages(system="s", msgs=[{"role": "user", "content": "x"}])
except Exception as e:
    текст_ошибки = str(e)
urllib.request.urlopen = _НАСТОЯЩИЙ_URLOPEN
chk("A · ключ не утёк в текст ошибки", os.environ["LEADBOT_API_KEY"] not in текст_ошибки, True)

# ── БЛОК Б. СКВОЗНЯК ЧЕРЕЗ БОЕВОЙ МОСТ LeadChat ─────────────────────────────
# Движок LeadChat не ретраит: что мы вернули, то клиент и получил. Проверяем на
# КАЖДОЙ беде оба вопроса сразу.
for имя, открывалка in БЕДЫ_ТРАНСПОРТА:
    сбросить()
    out, code = беда(имя, открывалка)
    reply = out.get("reply")
    # Сетевые беды (таймаут, 401, 429…) — транспортный уровень: движку уходит
    # unavailable, пустота здесь легитимна. Квитанция за номер (решение 30.08)
    # живёт уровнем выше — в ветке model_silent (блок В).
    chk("Б · %s: клиенту пустая строка" % имя, reply, "")
    chk("Б · %s: код 200 (не 500)" % имя, code, 200)
    chk("Б · %s: диалог человеку" % имя, out.get("needs_operator"), True)
    chk("Б · %s: уверенность ниже порога 0.6" % имя, out.get("confidence") < 0.6, True)
    отказы = журнал("leadbot.refused")
    chk("Б · %s: отказ записан в журнал" % имя, len(отказы) == 1, True)
    причина = (отказы[0].get("reason") or "") if отказы else ""
    chk("Б · %s: в журнале есть место падения" % имя,
        (".py:" in причина) and ("," in причина), True)
    chk("Б · %s: заявка в CRM не создана" % имя, очередь_crm(), [])

# ── БЛОК В. МОДЕЛЬ ОТВЕТИЛА, НО ОТВЕТА НЕТ ──────────────────────────────────
# Обрыв по max_tokens, текст вместо вызова инструмента, мусор в полях. Тут шлюз
# жив и код 200 — беда приходит содержимым, и клиенту нельзя отдать ни пустой
# пузырь, ни «42», ни половину служебной структуры.
ПУСТЫЕ = [
    ("нет вызова инструмента",
     json.dumps({"content": [{"type": "text", "text": "{\"reply\""}]})),
    ("пустой reply", ответ_модели("")),
    ("reply из пробелов", ответ_модели("   \n  ")),
    ("вход инструмента — строка",
     json.dumps({"content": [{"type": "tool_use", "name": "dispatcher_turn",
                              "input": "мусор вместо json"}]}, ensure_ascii=False)),
    ("reply числом", ответ_модели(42)),
    ("ответ шлюза не объект", "[1, 2, 3]"),
    ("ответ шлюза — пустой объект", "{}"),
]

for имя, тело in ПУСТЫЕ:
    сбросить()
    out, code = беда(имя, сеть_отвечает(тело))
    if имя == "ответ шлюза не объект":
        # этот вход валится ещё на разборе ответа (до ветки model_silent) — путь
        # ошибки, пустота легитимна как в блоке Б
        chk("В · %s: клиенту пустая строка (путь ошибки)" % имя, out.get("reply"), "")
    else:
        chk("В · %s: квитанция за номер вместо тишины (решение 30.08)" % имя,
            bool(out.get("reply")) and ("номер" in out.get("reply", "").lower()
                                        or "свяж" in out.get("reply", "").lower()),
            True)
    chk("В · %s: код 200" % имя, code, 200)
    chk("В · %s: диалог человеку" % имя, out.get("needs_operator"), True)
    chk("В · %s: заявка в CRM не создана" % имя, очередь_crm(), [])
    # причина молчания обязана быть в журнале: без неё строка «ответил длиной 0»
    # неотличима от штатной молчаливой передачи человеку (жалоба, чёрный список)
    строки = журнал("leadbot.answered") + журнал("leadbot.refused")
    видно = any((str(x.get("reason") or "") + str(x.get("error") or "")).strip() for x in строки)
    chk("В · %s: причина молчания видна в журнале" % имя, видно, True)
    # ⚠ И ЭТО ДОЛЖЕН БЫТЬ РАЗБОРЧИВЫЙ ОТКАЗ, А НЕ СЛУЧАЙНО ПОЙМАННОЕ ПАДЕНИЕ. Мост
    # LeadChat заворачивает в «оператор» вообще всё, и на нём разница незаметна; на пути
    # Пульта падение мимо ClaudeError не ловит никто — ответ не уходит совсем.
    chk("В · %s: мусор разобран, а не уронил ход" % имя,
        "AttributeError" in str(журнал("leadbot.refused")), False)

# ── БЛОК Г. ИСКЛЮЧЕНИЕ ВНУТРИ BRAIN (ошибка в гарде, битые данные) ───────────
# Подменяем НАСТОЯЩИЙ гард, который вызывает боевой путь, — а не выдуманную функцию.
сбросить()
_настоящий_гард = server.prefilter.enforce_callouts


def _гард_падает(*a, **k):
    раскладка = {"город": "Ульяновск"}
    return раскладка["нет-такого-ключа"]           # KeyError из середины конвейера


server.prefilter.enforce_callouts = _гард_падает
try:
    urllib.request.urlopen = сеть_отвечает(ответ_модели("Понял вас, что именно не работает?"))
    out, code = server.leadchat_answer(dict(ЗАПРОС, request_id="rid-гард"))
finally:
    server.prefilter.enforce_callouts = _настоящий_гард
    urllib.request.urlopen = _НАСТОЯЩИЙ_URLOPEN
chk("Г · гард упал: клиенту пустая строка", out.get("reply"), "")
chk("Г · гард упал: код 200", code, 200)
chk("Г · гард упал: диалог человеку", out.get("needs_operator"), True)
_отк = журнал("leadbot.refused")
chk("Г · гард упал: отказ в журнале", len(_отк) == 1, True)
_прич = (_отк[0].get("reason") or "") if _отк else ""
chk("Г · гард упал: назван тип ошибки", "KeyError" in _прич, True)
chk("Г · гард упал: названо место в brain", "server.py:" in _прич, True)
chk("Г · гард упал: заявка в CRM не создана", очередь_crm(), [])

# БИТЫЕ ДАННЫЕ ОТ ДВИЖКА: реплика блоками, а не строкой. Формат сообщений у моделей
# давно допускает [{"type":"text","text":"…"}], и адаптер LeadChat однажды пришлёт
# именно так. Разбор запроса живёт ДО защиты «любое исключение — оператор», поэтому
# такой запрос обязан либо разобраться, либо вернуть код и строку журнала — но не
# оборвать соединение молча.
сбросить()
_блочный = {"request_id": "rid-блоки", "city": "Ульяновск",
            "dialog": [{"role": "user",
                        "content": [{"type": "text", "text": "Холодильник не морозит"}]}]}
urllib.request.urlopen = сеть_отвечает(ответ_модели("Понял вас, что именно не работает?"))
try:
    out, code = server.leadchat_answer(_блочный)
    _вид = "ответил"
except Exception as e:
    out, code, _вид = {}, 0, type(e).__name__
finally:
    urllib.request.urlopen = _НАСТОЯЩИЙ_URLOPEN
chk("Г · блочный content не роняет мост", _вид, "ответил")
chk("Г · блочный content: текст реплики не потерян", bool(out.get("reply")), True)

# ── БЛОК Д. ТАЙМАУТ: БРОШЕННЫЙ ХОД НЕ ИМЕЕТ ПРАВА РОЖАТЬ ЗАЯВКУ ─────────────
# Мост отвечает «оператор» на своей секунде, но нить дорабатывает сама. Если она
# успевает создать заявку, в CRM ложится карточка с ВРЕМЕНЕМ ВИЗИТА из ответа,
# который клиент никогда не видел: мастер поедет на час, о котором не договаривались,
# а человек, ведущий диалог, создаст вторую заявку.
сбросить()
_готово = threading.Event()


def _медленный_шлюз(req, timeout=None):
    time.sleep(2.0)
    _готово.set()
    return _Тело(ответ_модели("Понял вас. Могу сегодня к 17:00 подъехать, устроит?",
                              category="bt", phone="89170001122",
                              address="Ленина 5, кв 2", preferred_time="сегодня 17:00"))


urllib.request.urlopen = _медленный_шлюз
try:
    out, code = server.leadchat_answer(dict(ЗАПРОС, request_id="rid-таймаут"))
    _готово.wait(6.0)
    time.sleep(1.0)                                 # дать брошенной нити доработать
finally:
    urllib.request.urlopen = _НАСТОЯЩИЙ_URLOPEN
chk("Д · таймаут: клиенту пустая строка", out.get("reply"), "")
chk("Д · таймаут: диалог человеку", out.get("needs_operator"), True)
chk("Д · таймаут: отказ записан отдельно от падения",
    bool([x for x in журнал("leadbot.refused") if "таймаут" in str(x.get("reason"))]), True)
_очередь = очередь_crm()
chk("Д · таймаут: фантомной заявки нет", len(_очередь), 0)
chk("Д · таймаут: не назначен визит, о котором клиент не знает",
    any((z.get("lead") or {}).get("visit_time") for z in _очередь), False)

# контроль: тот же ход БЕЗ таймаута заявку создаёт — иначе проверка выше
# доказывала бы лишь то, что лид не собирается вообще
сбросить()
os.environ["LEADBOT_ANSWER_TIMEOUT_S"] = "30"
urllib.request.urlopen = сеть_отвечает(
    ответ_модели("Понял вас. Могу сегодня к 17:00 подъехать, устроит?",
                 category="bt", phone="89170001122", address="Ленина 5, кв 2",
                 preferred_time="сегодня 17:00"))
try:
    out, code = server.leadchat_answer(dict(ЗАПРОС, request_id="rid-успех"))
    time.sleep(0.5)
finally:
    urllib.request.urlopen = _НАСТОЯЩИЙ_URLOPEN
    os.environ["LEADBOT_ANSWER_TIMEOUT_S"] = "1"
chk("Д · контроль: успешный ход отвечает клиенту", bool(out.get("reply")), True)
chk("Д · контроль: успешный ход создаёт заявку", len(очередь_crm()), 1)

# ── БЛОК Е. АВТОКАНАЛ AVITO: ОТКАЗ МОЗГА НА ЖИВОМ АККАУНТЕ ──────────────────
# Здесь бот отправляет сам, без человека посередине. Пустой черновик не имеет
# права уехать в чат, чат обязан остаться помеченным как неотвеченный, а причина
# отказа — попасть в журнал так же, как на мосту LeadChat.
сбросить()
ОТПРАВЛЕНО = []
avito_api.self_id = lambda acc: "мы"
avito_api.messages = lambda acc, cid: [
    {"id": "m1", "author_id": "он", "created": time.time() - 60,
     "content": {"text": "Холодильник не морозит"}}]
avito_api.send = lambda acc, cid, txt: ОТПРАВЛЕНО.append(txt)
avito_api.mark_read = lambda acc, cid: None
АККАУНТ = {"id": "a1", "enabled": True, "auto_reply": True,
           "city": "Ульяновск", "direction": "bt"}
ЧАТ = {"id": "c1", "context": {"value": {"id": "i1", "title": "Ремонт холодильников"}},
       "users": [{"id": "он", "name": "Ваня"}]}

ВЫЗОВОВ = []


def _мозг_не_смог(messages, known=None):
    ВЫЗОВОВ.append(1)
    raise claude_api.ClaudeError("HTTP 429: rate_limit_error")


channel._SUGGEST = _мозг_не_смог
channel._DEBOUNCE = None
channel._STATE.pop("a1:c1", None)
try:
    channel._process_chat(АККАУНТ, ЧАТ, time.time())
    _вид = "молча проглотил"
except Exception:
    _вид = "поднял наверх"
ст = channel._chat_state("a1:c1")
chk("Е · клиенту не отправлено ничего", ОТПРАВЛЕНО, [])
chk("Е · черновик остался пустым", ст.get("draft"), "")
chk("Е · чат помечен неотвеченным (виден человеку)", ст.get("unanswered"), True)
chk("Е · отказ поднят в обход опроса", _вид, "поднял наверх")
# ⚠ ПОВТОР ПЕРЕЕХАЛ В КЛИЕНТ (24.08), И ЗДЕСЬ ЕГО БОЛЬШЕ НЕТ. Раньше автоканал делал
# свой второй заход, потому что у claude_api ретраев не было вовсе. Теперь они есть, и
# держать оба слоя нельзя: они ПЕРЕМНОЖАЮТСЯ.
chk("Е · автоканал зовёт мозг один раз, повтор живёт ниже", len(ВЫЗОВОВ), 1)

# ── ПОТОЛОК ОБРАЩЕНИЙ К ПРОВАЙДЕРУ НА УПОРНОМ ОТКАЗЕ ────────────────────────
# ⚠ ГЛАВНОЕ ПРАВИЛО ЭТОГО МЕСТА. Слоёв повтора в системе три: круг опроса автоканала,
# попытки внутри клиента и запасная модель. Если каждый добавляет свой заход, они
# перемножаются: до восьми обращений на одном отказе. Провайдер на 429 отвечает 429 и
# на восьмой раз — мы своими руками усиливаем ту самую перегрузку, из-за которой он и
# отказал. Поэтому считаем НЕ «есть ли повтор», а СКОЛЬКО ВСЕГО ударов по сети.
_УДАРОВ = {"n": 0}


def _всегда_429(req, timeout=None):
    _УДАРОВ["n"] += 1
    raise urllib.error.HTTPError("https://api.anthropic.com/v1/messages", 429,
                                 "err", {}, io.BytesIO(b'{"error":"rate"}'))


_ПАУЗЫ_БЫЛИ = claude_api.time.sleep
claude_api.time.sleep = lambda _s: None
urllib.request.urlopen = _всегда_429
try:
    try:
        server._call_model("сис", [{"role": "user", "content": "не морозит"}],
                           claude_api.config().get("model_hard") or "claude-sonnet-5")
    except claude_api.ClaudeError:
        pass
    # 2 попытки сильной модели + 1 заход запасной = 3. Больше — значит слои снова
    # перемножаются, и правку надо искать, а не терпеть.
    chk("Е · ударов по сети на упорном 429 не больше трёх", _УДАРОВ["n"] <= 3, True)
    chk("Е · и не меньше двух: повтор всё-таки есть", _УДАРОВ["n"] >= 2, True)
finally:
    claude_api.time.sleep = _ПАУЗЫ_БЫЛИ
    urllib.request.urlopen = _НАСТОЯЩИЙ_URLOPEN
_отк_канал = журнал("leadbot.refused")
chk("Е · отказ автоканала записан в журнал", len(_отк_канал) >= 1, True)
chk("Е · в журнале автоканала есть место падения",
    any(".py:" in str(x.get("reason") or "") for x in _отк_канал), True)

# затяжной отказ: через 5 минут — крик просрочки с устойчивым префиксом
сбросить()
ст["brain_fail_since"] = time.time() - 400
ст.pop("brain_down_notified", None)
_вывод = io.StringIO()
_прежний = sys.stdout
sys.stdout = _вывод
try:
    channel._process_chat(АККАУНТ, ЧАТ, time.time())
except Exception:
    pass
finally:
    sys.stdout = _прежний
chk("Е · через 5 минут молчания — крик просрочки",
    "[ЭСКАЛАЦИЯ-ПРОСРОЧКА]" in _вывод.getvalue() and "мозг_недоступен" in _вывод.getvalue(),
    True)

# старый черновик не подменяет собой новый ход: клиент не должен получить ответ
# на ПРЕДЫДУЩЕЕ сообщение под видом ответа на нынешнее
ст["draft"] = "Здравствуйте, что случилось?"
ст["draft_for_msg"] = "m0"
del ОТПРАВЛЕНО[:]
try:
    channel._process_chat(АККАУНТ, ЧАТ, time.time())
except Exception:
    pass
chk("Е · старый черновик не уехал клиенту", ОТПРАВЛЕНО, [])

# ── итог ────────────────────────────────────────────────────────────────────
# ── ПОВТОР НА ВРЕМЕННОМ ОТКАЗЕ (правка 24.08) ────────────────────────────────
# Ретраев не было НИ ОДНОГО: любой 429 от провайдера сразу поднимался ClaudeError,
# наверху становился 502, и клиент получал молчание. А 429 у нас не редкость по
# устройству обхода: опрос раз в 12 секунд, до 30 чатов на аккаунт, два аккаунта —
# замеренный пик 33 вызова в минуту при лимите 20.
#
# Бюджет хода это не ломает: девять секунд держатся снаружи, через th.join в
# server.leadchat_answer, и не успевшую нить там БРОСАЮТ, а не убивают — она
# дорабатывает и кладёт ответ в кэш, который пригодится на следующем ходу.
_ПАУЗЫ_НАСТОЯЩИЕ = claude_api.time.sleep


def _без_пауз():
    claude_api.time.sleep = lambda _s: None


def _вернуть_паузы():
    claude_api.time.sleep = _ПАУЗЫ_НАСТОЯЩИЕ


def сеть_падает_потом_отвечает(код, падений, тело_ответа):
    """Первые `падений` попыток — HTTP-код, дальше нормальный ответ."""
    счёт = {"n": 0}

    def _open(req, timeout=None):
        счёт["n"] += 1
        if счёт["n"] <= падений:
            raise urllib.error.HTTPError("https://api.anthropic.com/v1/messages", код,
                                         "err", {}, io.BytesIO(b'{"error":"temp"}'))
        return _Тело(тело_ответа)
    _open.счёт = счёт
    return _open


def _вызов():
    return claude_api.messages("сис", [{"role": "user", "content": "не морозит"}])


_без_пузов = None
_без_пауз()
try:
    # 429 один раз → второй попыткой получаем ответ, наверх ошибка НЕ летит
    _откр = сеть_падает_потом_отвечает(429, 1, ответ_модели("гляну"))
    urllib.request.urlopen = _откр
    try:
        _ответ = _вызов()
        chk("B · 429 повторяется и ход доходит", bool(_ответ.get("content")), True)
        chk("B · повтор был ровно один", _откр.счёт["n"], 2)
    except Exception as e:                                    # noqa: BLE001
        chk("B · 429 повторяется и ход доходит", "ошибка: %s" % e, True)

    # 500 и 529 (overloaded) — тоже временные
    for _код in (500, 529, 503):
        _o = сеть_падает_потом_отвечает(_код, 1, ответ_модели("гляну"))
        urllib.request.urlopen = _o
        try:
            _вызов()
            chk("B · %d повторяется" % _код, _o.счёт["n"], 2)
        except Exception:                                     # noqa: BLE001
            chk("B · %d повторяется" % _код, "не повторился", 2)

    # ⚠ 401 и 400 ПОВТОРЯТЬ НЕЛЬЗЯ: ключ и формат за секунду не починятся, а бюджет
    # хода съедят. Повтор здесь — это удвоенная задержка перед тем же отказом.
    for _код in (400, 401, 403):
        _o = сеть_падает_потом_отвечает(_код, 1, ответ_модели("гляну"))
        urllib.request.urlopen = _o
        try:
            _вызов()
            chk("B · %d НЕ повторяется" % _код, "ответ пришёл", 1)
        except claude_api.ClaudeError:
            chk("B · %d НЕ повторяется" % _код, _o.счёт["n"], 1)

    # сеть оборвалась — повторяем, это тот же временный отказ
    _сч = {"n": 0}

    def _рвётся_потом_отвечает(req, timeout=None):
        _сч["n"] += 1
        if _сч["n"] == 1:
            raise urllib.error.URLError("Connection reset")
        return _Тело(ответ_модели("гляну"))

    urllib.request.urlopen = _рвётся_потом_отвечает
    try:
        _вызов()
        chk("B · обрыв сети повторяется", _сч["n"], 2)
    except Exception:                                          # noqa: BLE001
        chk("B · обрыв сети повторяется", "не повторился", 2)

    # ...и если отказ УПОРНЫЙ, наверх по-прежнему летит ClaudeError, а не что-то ещё:
    # пять мест в server.py ловят именно её и отвечают 502
    urllib.request.urlopen = сеть_кодом(429, '{"error":"rate"}')
    try:
        _вызов()
        chk("B · упорный 429 → ClaudeError", "ответ пришёл", "ClaudeError")
    except claude_api.ClaudeError:
        chk("B · упорный 429 → ClaudeError", "ClaudeError", "ClaudeError")
    except Exception as e:                                     # noqa: BLE001
        chk("B · упорный 429 → ClaudeError", type(e).__name__, "ClaudeError")

    # Retry-After провайдера главнее нашей лестницы, но не дольше пяти секунд
    class _ОшибкаСЗаголовком(urllib.error.HTTPError):
        pass

    chk("B · retry-after уважается", claude_api._пауза(
        1, type("E", (), {"headers": {"retry-after": "3"}})()), 3.0)
    chk("B · retry-after больше пяти секунд обрезается", claude_api._пауза(
        1, type("E", (), {"headers": {"retry-after": "60"}})()), 5.0)
    chk("B · без заголовка — своя лестница", claude_api._пауза(
        1, type("E", (), {"headers": {}})()), claude_api._ПАУЗЫ[1])
finally:
    _вернуть_паузы()
    urllib.request.urlopen = _НАСТОЯЩИЙ_URLOPEN


# ── ДЕГРАДАЦИЯ СИЛЬНОЙ МОДЕЛИ НА ПРОСТУЮ (правка 24.08) ─────────────────────
# Повторы лечат икоту сети, но если провайдер упорно отдаёт 429 именно по Sonnet,
# повторять нечего: очередь общая на модель. Честнее ответить Haiku, чем не ответить
# вовсе — она проходит те же сорок гардов и тот же регламент.
_HARD = claude_api.config().get("model_hard") or "claude-sonnet-5"
_SIMPLE = claude_api.config().get("model_simple") or ""

for _о, _ждём in (("HTTP 429: rate limited", _SIMPLE),
                  ("HTTP 529: overloaded", _SIMPLE),
                  ("HTTP 503: unavailable", _SIMPLE),
                  ("Сеть: timeout", _SIMPLE),
                  # ⚠ НА ЭТИХ ПАДАТЬ НЕЛЬЗЯ: у Haiku будет ровно та же беда, а ход
                  # потеряет вторую попытку впустую
                  ("HTTP 401: authentication_error", ""),
                  ("HTTP 400: invalid_request", ""),
                  ("HTTP 403: forbidden", "")):
    chk("C · %s → %s" % (_о[:26], _ждём or "не падаем"),
        server._запасная_модель(_HARD, _о), _ждём)

# с простой модели падать некуда — иначе получится кольцо
chk("C · с простой модели падать некуда",
    server._запасная_модель(_SIMPLE, "HTTP 429: rate limited"), "")

# и сквозняк: Sonnet отказывает упорно, Haiku отвечает — клиент получает ответ
_счёт = {"sonnet": 0, "haiku": 0}
_НАСТ_MSG = claude_api.messages


def _по_модели(system=None, msgs=None, tools=None, tool_choice=None, model=None, **kw):
    if model == _HARD:
        _счёт["sonnet"] += 1
        raise claude_api.ClaudeError("HTTP 529: overloaded")
    _счёт["haiku"] += 1
    return json.loads(ответ_модели("гляну, что случилось"))


claude_api.messages = _по_модели
try:
    _resp, _out = server._call_model("сис", [{"role": "user", "content": "не морозит"}], _HARD)
    chk("C · сквозняк: сильная отказала, ответ всё равно есть", bool(_out), True)
    chk("C · сквозняк: сильную звали один раз", _счёт["sonnet"], 1)
    chk("C · сквозняк: простую звали один раз", _счёт["haiku"], 1)
except Exception as e:                                        # noqa: BLE001
    chk("C · сквозняк: сильная отказала, ответ всё равно есть", "ошибка %s" % e, True)
finally:
    claude_api.messages = _НАСТ_MSG

# ── ЛИМИТЫ ПРОВАЙДЕРА СНИМАЮТСЯ С ЗАГОЛОВКОВ (правка 24.08) ────────────────
# Anthropic присылает в заголовках каждого ответа потолок и остаток по запросам и
# токенам. Мы читали только тело, а заголовки выбрасывали — и вопрос «упираемся ли мы
# в лимит» решался гаданием по пикам в журнале расходов. Гадание вышло неверным: пики
# в локальном журнале оказались следом прогонов, а не боя. Замер теперь даровой.
class _ТелоСЗаголовками:
    def __init__(self, текст, заголовки):
        self._b = текст.encode("utf-8")
        self.headers = заголовки

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_ЗАГ = {"anthropic-ratelimit-requests-limit": "4000",
        "anthropic-ratelimit-requests-remaining": "3987",
        "anthropic-ratelimit-input-tokens-limit": "400000",
        "anthropic-ratelimit-input-tokens-remaining": "351200",
        "x-request-id": "req_1"}
claude_api.ЛИМИТЫ.clear()
urllib.request.urlopen = lambda req, timeout=None: _ТелоСЗаголовками(ответ_модели("гляну"), _ЗАГ)
try:
    claude_api.messages("сис", [{"role": "user", "content": "не морозит"}])
    chk("Ж · лимиты сняты с ответа", claude_api.ЛИМИТЫ.get("requests-remaining"), "3987")
    chk("Ж · потолок запросов сохранён", claude_api.ЛИМИТЫ.get("requests-limit"), "4000")
    chk("Ж · токенный лимит тоже", claude_api.ЛИМИТЫ.get("input-tokens-remaining"), "351200")
    # ⚠ ЧУЖИЕ ЗАГОЛОВКИ НЕ ТАЩИМ: журнал расходов читают шире, чем переписку
    chk("Ж · посторонние заголовки не попадают", "x-request-id" in claude_api.ЛИМИТЫ, False)

    # ⚠ ОТВЕТ БЕЗ ЗАГОЛОВКОВ НЕ ДОЛЖЕН РОНЯТЬ ХОД. Объект ответа их иметь не обязан:
    # прокси, чужая обёртка, заглушка. Падать из-за замера расходов недопустимо.
    _раньше = dict(claude_api.ЛИМИТЫ)
    urllib.request.urlopen = сеть_отвечает(ответ_модели("гляну"))
    claude_api.messages("сис", [{"role": "user", "content": "не морозит"}])
    chk("Ж · ответ без заголовков ход не роняет", True, True)
    chk("Ж · прежние лимиты при этом уцелели", claude_api.ЛИМИТЫ, _раньше)
except Exception as e:                                        # noqa: BLE001
    chk("Ж · лимиты сняты с ответа", "упало: %s" % type(e).__name__, "3987")
finally:
    urllib.request.urlopen = _НАСТОЯЩИЙ_URLOPEN

ok = 0
for name, got, want in CASES:
    if got == want:
        ok += 1
    else:
        print("  ПРОВАЛ: %-52s ждали=%r получили=%r" % (name, want, got))
if СТРАЖ_ПЛАТНОСТИ[0]:
    print("  (набор шёл под стражем платности: сетевые беды не подавались)")
print("=" * 72)
print("Отказ хода (шлюз, brain, мусор, таймаут, автоканал): %d/%d" % (ok, len(CASES)))
sys.exit(0 if ok == len(CASES) else 1)
