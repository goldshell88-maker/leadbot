# -*- coding: utf-8 -*-
"""СВОЙ КАНАЛ AVITO — сердце отдельной панели бота (решение заказчика: без Jivo и без Чат Хаба).

Что здесь живёт:
  • хранилище АККАУНТОВ (data/avito_accounts.json): client_id/secret + персона, направление,
    партнёр/белый, город по умолчанию, тумблеры «включён» и «автоответ»;
  • РЕЕСТР ОБЪЯВЛЕНИЙ (data/ad_registry.json): item_id → направление/партнёр/белый/город/персона —
    это контракт канала из сессии 41e (метка Jivo умерла, направление даёт реестр);
  • ОПРОСЧИК: фоновый поток раз в POLL_SEC обходит включённые аккаунты, тянет чаты и сообщения,
    строит known и зовёт мозг (server.run_relay через колбэк — без циклического импорта);
  • ЧЕРНОВИКИ и АВТООТВЕТ: по умолчанию бот только предлагает (панель показывает черновик,
    оператор жмёт «Отправить»); автоответ включается ПО АККАУНТУ и шлёт сам после дебаунса
    server.debounce_wait. Пустой ответ/жалоба/чёрный список НЕ отправляются никогда.

⚠ ПЕРСОНА: у аккаунта постоянный голос (правило 41e «аккаунт → голос с первого дня»).
⚠ PII: секреты аккаунтов лежат локально в data/, наружу не ходят никуда, кроме api.avito.ru.
"""
import json
import os
import threading
import time
import hashlib
import re

import paths
import avito_api
import logbook
import prefilter

ACCOUNTS_FILE = os.path.join(paths.ROOT, "data", "avito_accounts.json")
REGISTRY_FILE = os.path.join(paths.ROOT, "data", "ad_registry.json")
STATE_FILE = os.path.join(paths.ROOT, "data", "channel_state.json")

POLL_SEC = 12          # период обхода аккаунтов
DEBOUNCE_CAP = 40      # предохранитель: дольше этого автоответ не ждёт никогда
# ── ПИНГ МОЛЧАЩЕМУ И «РЕШЁН» (регламент, решение заказчика 15.08) ─────────────
# «Если после вашего ответа клиент не отвечает — подождать 5 минут и задать
# уточняющий вопрос; спустя ещё 10 минут тишины пометить диалог решённым».
# Пинг СТРОГО один на цикл молчания (настырность = жалобы на Авито), только на
# аккаунтах с автоответом и только днём; «Решён» — внутренняя пометка панели,
# в Авито ничего не уходит, новое сообщение клиента оживляет диалог.
#: ⚠ СРОК И ЧАСТОТА ДОЖИМА — ПО ЖИВЫМ, А НЕ «ЧЕРЕЗ ПЯТЬ МИНУТ» (21.08).
#: Свежий корпус Jivo, 27 786 диалогов, 3202 настоящих дожима (пауза оператора
#: больше двух минут без ответа клиента): p25 = 11.3 мин, медиана 25.2 мин,
#: p75 = 45.5 мин; быстрее пяти минут — 11% дожимов. Бот пинговал ровно через пять
#: минут и в КАЖДОМ молчащем диалоге, живой пингует 17.3% диалогов.
#: Поэтому две величины, а не одна: срок вразброс и вентиль вероятности. Без
#: вентиля перенос срока снижает охват всего с 90% до 88% — молчание длиннее пяти
#: минут случается почти в каждом диалоге. Регламент связывает настырность с
#: жалобами на Авито и блокировкой аккаунта, так что это не косметика.
PING_MIN = 12 * 60
PING_MAX = 25 * 60
PING_CHANCE = 20                   # процентов молчаний, которые получают дожим
PING_AFTER = PING_MIN              # обратная совместимость: нижняя граница окна
PING_MAX_AGE = 2 * 3600   # старше — пинг неуместен (аудит 16.08)
RESOLVE_AFTER_PING = 10 * 60
PING_HOURS = range(9, 22)          # по локальным часам ГОРОДА КЛИЕНТА (см. _silence_watch)


def _зерно(ключ, соль):
    import hashlib
    return int(hashlib.sha256(("%s|%s" % (соль, ключ or "")).encode("utf-8")).hexdigest(), 16)


def ping_after(ключ=""):
    """Сколько ждать до дожима в ЭТОМ чате: 12–25 минут, стабильно по ключу.

    Одинаковая для всех пауза выдаёт автомат сама по себе: у живых разброс
    огромный. Ключ — чат плюс id последнего сообщения, поэтому повторный проход
    опросчика по тому же молчанию даёт то же число, а новый цикл — новое.
    """
    return PING_MIN + _зерно(ключ, "срок") % (PING_MAX - PING_MIN + 1)


def ping_allowed(ключ=""):
    """Дожимать ли это молчание вообще. Примерно каждое пятое — как у живых.

    Соль отличается от срока намеренно: иначе «долгая пауза» и «пингуем» решались
    бы одной монеткой, и все дожимы легли бы в один конец диапазона.
    """
    return _зерно(ключ, "вентиль") % 100 < PING_CHANCE
PING_POOL = ["Ну что, актуально?",
             "Подскажете? Сразу сориентирую вас",
             "На связи. Если актуально — продолжим"]

#: ⚠ ПИНГ НЕСЁТ СЛЕДУЮЩИЙ ШАГ (этап 2 программы обучения, 27 710 живых диалогов).
#: 2029 диалогов у живых диспетчеров заканчиваются голым «актуально?/в силе?» — это
#: дешёвая замена работе: клиент уже молчит, и вопрос «ты ещё здесь?» не даёт ему
#: НИЧЕГО нового. Подъём дают конкретные ходы: просьба номера +13.2 п.п. к лиду,
#: вопрос про марку-модель +7.5, «куда подъехать» +7.3. Поэтому пинг выбирается по
#: тому, чего в диалоге НЕ ХВАТАЕТ, а «актуально?» остаётся только когда есть уже всё.
PING_БЕЗ_НОМЕРА = ["Подскажите ваш номер, и договоримся по времени",
                   "Оставьте номер, пожалуйста, и я сразу подтвержу выезд",
                   "Скиньте номер — договоримся по времени"]
PING_БЕЗ_ОКНА = ["Подскажите, когда вам удобно — подъеду",
                 "На какое время вам удобнее, чтобы я подъехал?",
                 "Скажите удобное время — сориентирую по приезду"]
_LOCK = threading.RLock()
_STATE = {}            # chat_key -> {...} (см. _chat_state)
_ACCOUNTS = None       # кэш файла аккаунтов
_REGISTRY = None
_SUGGEST = None        # колбэк server.run_relay(messages, known=...) — ставит start()
_DEBOUNCE = None       # колбэк server.debounce_wait(last_client, seconds_since_last)
_THREAD = None
_LAST_ERRORS = {}      # account_id -> текст последней ошибки опроса (для панели)


# ---------- файловые хранилища ----------

def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def accounts(reload=False):
    global _ACCOUNTS
    with _LOCK:
        if _ACCOUNTS is None or reload:
            _ACCOUNTS = _load_json(ACCOUNTS_FILE, [])
        return _ACCOUNTS


def save_account(acc):
    """Добавить/обновить аккаунт. id — стабильный хеш client_id."""
    with _LOCK:
        accs = accounts()
        acc = dict(acc)
        cid = (acc.get("client_id") or "").strip()
        if not cid:
            raise ValueError("client_id пуст")
        acc["id"] = acc.get("id") or hashlib.sha256(cid.encode()).hexdigest()[:10]
        acc.setdefault("label", "Аккаунт " + acc["id"][:4])
        acc.setdefault("persona", _persona_for(acc["id"]))
        acc.setdefault("direction", "")
        acc.setdefault("partner", "")
        acc.setdefault("white", False)
        acc.setdefault("city", "")
        acc.setdefault("enabled", True)
        acc.setdefault("auto_reply", False)     # ⚠ автоответ ВСЕГДА выключен по умолчанию
        for i, a in enumerate(accs):
            if a.get("id") == acc["id"]:
                # секрет не затираем пустым (панель шлёт маску при правке настроек)
                if not (acc.get("client_secret") or "").strip():
                    acc["client_secret"] = a.get("client_secret", "")
                accs[i] = acc
                break
        else:
            accs.append(acc)
        _save_json(ACCOUNTS_FILE, accs)
        return acc


def delete_account(acc_id):
    with _LOCK:
        accs = [a for a in accounts() if a.get("id") != acc_id]
        globals()["_ACCOUNTS"] = accs
        _save_json(ACCOUNTS_FILE, accs)


def registry(reload=False):
    global _REGISTRY
    with _LOCK:
        if _REGISTRY is None or reload:
            _REGISTRY = _load_json(REGISTRY_FILE, {})
        return _REGISTRY


def save_registry_item(item_id, fields):
    with _LOCK:
        reg = registry()
        item_id = str(item_id)
        cur = reg.get(item_id) or {}
        cur.update({k: v for k, v in fields.items()
                    if k in ("direction", "partner", "white", "city", "persona", "title")})
        reg[item_id] = cur
        _save_json(REGISTRY_FILE, reg)
        return cur


def _persona_for(seed):
    """Стабильный голос аккаунта p1..p10 (та же схема, что _persona_for в jivo_live)."""
    n = int(hashlib.sha256(str(seed).encode()).hexdigest(), 16) % 10 + 1
    return "p%d" % n


# ---------- состояние чатов ----------

def _chat_key(acc_id, chat_id):
    return "%s:%s" % (acc_id, chat_id)


def _chat_state(key):
    st = _STATE.get(key)
    if st is None:
        st = {"history": [], "last_msg_id": None, "last_client_ts": 0, "draft": "",
              "draft_for_msg": None, "handoff": False, "note": "", "flag": "",
              "sent_ids": [], "auto_sent": 0, "title": "", "visitor": "", "item_id": "",
              "updated": 0, "unanswered": False,
              "ping_at": 0, "resolved": False}
        _STATE[key] = st
    return st


def _persist_state():
    """Лёгкий снапшот (без полных историй — их отдаёт Avito), переживает рестарт."""
    with _LOCK:
        slim = {}
        for k, st in _STATE.items():
            slim[k] = {kk: st.get(kk) for kk in
                       ("last_msg_id", "last_client_ts", "draft", "draft_for_msg", "handoff",
                        "note", "flag", "auto_sent", "title", "visitor", "item_id",
                        "updated", "unanswered", "ping_at", "resolved",
                        # сторож просрочек обязан пережить выкатку — иначе каждый
                        # деплой обнулял эскалации и просрочка не сигналилась
                        "escalation", "escalation_at", "notify_human",
                        "overdue_notified", "last_manual_ts")}
        _save_json(STATE_FILE, slim)


def _restore_state():
    with _LOCK:
        for k, st in _load_json(STATE_FILE, {}).items():
            _chat_state(k).update(st)


# ---------- сборка known (контракт канала, сессия 41e) ----------

def _known_for(acc, chat):
    ctx = ((chat.get("context") or {}).get("value") or {})
    item_id = str(ctx.get("id") or "")
    reg = registry().get(item_id) or {}
    users = chat.get("users") or []
    my_id = None
    try:
        my_id = avito_api.self_id(acc)
    except Exception:
        pass
    visitor, visitor_id = "", ""
    for u in users:
        if u.get("id") != my_id:
            visitor = (u.get("name") or "").strip()
            visitor_id = str(u.get("id") or "")
    white = bool(reg.get("white", acc.get("white")))
    partner = str(reg.get("partner") or acc.get("partner") or "")
    return {
        "visitor": visitor,
        "city": reg.get("city") or acc.get("city") or "",
        "title": (ctx.get("title") or "").strip(),
        "direction": reg.get("direction") or acc.get("direction") or "",
        "persona": reg.get("persona") or acc.get("persona") or _persona_for(acc.get("id")),
        "partner": partner,
        "white": white or partner == "723",
        # для multi_account (правило «>5 наших аккаунтов»): собеседник + наш аккаунт
        "avito_uid": visitor_id,
        "widget_id": acc.get("id"),
    }, item_id


def _history_from_avito(acc, msgs):
    """Avito-сообщения → формат run_relay [{role: client/bot, text}]."""
    my_id = avito_api.self_id(acc)
    hist = []
    for m in msgs:
        txt = ""
        c = m.get("content") or {}
        if isinstance(c, dict):
            txt = (c.get("text") or "").strip()
            if not txt and c.get("image"):
                txt = "[фото]"
        if not txt:
            continue
        role = "bot" if m.get("author_id") == my_id else "client"
        hist.append({"role": role, "text": txt, "ts": m.get("created") or 0,
                     "id": str(m.get("id") or "")})
    return hist


# ---------- обработка одного чата ----------

def _silence_watch(acc, chat, st, last, now):
    """Регламентная пауза: 5 минут тишины → один пинг, ещё 10 → пометка «Решён».

    Пингуем только вслед СВОЕМУ ходу на аккаунте с автоответом и только когда
    последний ход не звал человека: эскалация/handoff — очередь человека, влезать
    пингом поперёк нельзя. Ночью молчим — пинг в три часа ночи хуже тишины.
    """
    if st.get("resolved"):
        return
    if st.get("ping_at"):
        if now - st["ping_at"] >= RESOLVE_AFTER_PING:
            st["resolved"] = True
            print("[РЕШЁН] чат=%s: тишина после пинга %d мин" %
                  (chat.get("id"), int((now - st["ping_at"]) / 60)), flush=True)
        return
    ts = last.get("ts") or 0
    ключ_пинга = "%s|%s" % (chat.get("id"), last.get("id"))
    if not ts or now - ts < ping_after(ключ_пинга):
        return
    if not ping_allowed(ключ_пинга):
        return
    if now - ts > PING_MAX_AGE:
        # тишина старше двух часов — пинг уже неуместен (и включение автоответа
        # не должно давать залп пингов по всем лежалым диалогам)
        return
    if not acc.get("auto_reply"):
        return
    if st.get("handoff") or st.get("notify_human") or st.get("escalation"):
        return
    # окно пинга — по часам ГОРОДА КЛИЕНТА (решение 15.08): по серверным часам день,
    # а во Владивостоке ночь — пинг в три часа ночи хуже тишины
    import territory
    if territory.city_localtime(st.get("city") or "", now).tm_hour not in PING_HOURS:
        return
    txt = ping_text(
        есть_телефон=bool(prefilter.phones_in(
            " ".join(m.get("text") or "" for m in st.get("history") or []
                     if m.get("role") == "client"))),
        есть_окно=bool(_ОКНО_RX.search(
            " ".join(m.get("text") or "" for m in st.get("history") or []
                     if m.get("role") != "client"))),
        ключ=ключ_пинга)
    avito_api.send(acc, chat.get("id"), txt)
    st["ping_at"] = now
    st["auto_sent"] = (st.get("auto_sent") or 0) + 1
    st["history"].append({"role": "bot", "text": txt, "ts": now, "id": "ping"})
    print("[ПИНГ] чат=%s: тишина %d мин, отправлено «%s»" %
          (chat.get("id"), int((now - ts) / 60), txt), flush=True)


#: окно в речи бота/оператора — те же признаки, что и в server._ОКНО_В_РЕПЛИКЕ_RX
_ОКНО_RX = re.compile(
    r"\d{1,2}[:.]\d{2}|\bк\s*\d{1,2}\b|час[аоу]?[-\s]*полтора|\bчерез\s+час|"
    r"\bчерез\s+\d|\bсегодня\b|\bзавтра\b|\bпослезавтра\b", re.IGNORECASE)


def ping_text(есть_телефон, есть_окно, ключ=""):
    """Текст пинга по тому, чего в диалоге не хватает (этап 2).

    Выбор внутри пула — стабильный по ключу чата, а не случайный: один и тот же
    диалог всегда получает одну формулировку (иначе повторный пинг выглядит как
    сбой), а РАЗНЫЕ чаты получают разные — это требование несвязываемости
    аккаунтов от 17.08.
    """
    пул = PING_БЕЗ_НОМЕРА if not есть_телефон else (PING_БЕЗ_ОКНА if not есть_окно else PING_POOL)
    h = int(hashlib.sha256((ключ or "").encode()).hexdigest(), 16)
    return пул[h % len(пул)]


def _process_chat(acc, chat, now):
    key = _chat_key(acc.get("id"), chat.get("id"))
    st = _chat_state(key)
    msgs = avito_api.messages(acc, chat.get("id"))
    hist = _history_from_avito(acc, msgs)
    if not hist:
        return
    known, item_id = _known_for(acc, chat)
    st["title"], st["visitor"], st["item_id"] = known["title"], known["visitor"], item_id
    st["city"] = known.get("city") or ""
    st["history"] = hist
    st["updated"] = now
    # неразмеченное объявление — показать в панели для разметки
    if item_id and item_id not in registry():
        save_registry_item(item_id, {"title": known["title"]})
    last = hist[-1]
    st["unanswered"] = last["role"] == "client"
    if last["role"] == "client":
        # клиент ожил — циклы молчания начинаются заново
        st["ping_at"], st["resolved"] = 0, False
    if last["role"] != "client":
        _silence_watch(acc, chat, st, last, now)  # пинг через 5 мин, «Решён» ещё через 10
        return                                   # последним говорили мы — ждём клиента
    if st.get("draft_for_msg") == last["id"] and st.get("draft"):
        pass                                     # черновик уже посчитан для этого сообщения
    else:
        # дебаунс: клиент ещё дописывает? (и для черновика тоже — не жечь вызовы на серии)
        since = max(0.0, now - (last.get("ts") or now))
        wait = 0
        if _DEBOUNCE:
            try:
                wait = _DEBOUNCE(last["text"], seconds_since_last=since)
            except Exception:
                wait = 0
        if wait > 0 and since < DEBOUNCE_CAP:
            return                               # придём следующим кругом опроса
        # ⚠ ОТКАЗ МОЗГА БОЛЬШЕ НЕ БЕЗЗВУЧЕН (аудит 15.08). Раньше исключение уходило
        # в _LAST_ERRORS — строку панели, которую надо открыть, чтобы увидеть. Клиент
        # в автоканале молчал неограниченно долго: сторож очереди следит только за
        # эскалациями, а «мозг падает» эскалацией не был.
        #
        # ⚠ СВОЕГО ПОВТОРА ЗДЕСЬ БОЛЬШЕ НЕТ (24.08). Он стоял тут потому, что у
        # claude_api ретраев не было вовсе, и это было НАМЕРЕННО: путь LeadChat живёт в
        # бюджете девяти секунд. Довод оказался неполным — бюджет держится снаружи через
        # th.join, и не успевшую нить там БРОСАЮТ, а не убивают: она дорабатывает и греет
        # кэш ответов. Поэтому повтор переехал в claude_api._послать, к самой сети, где он
        # видит HTTP-код и Retry-After.
        #
        # Держать оба слоя нельзя, и это не вкусовщина: они ПЕРЕМНОЖАЮТСЯ. Два круга здесь
        # × две попытки в клиенте × запасная модель = до восьми обращений к провайдеру на
        # одном упорном отказе. Провайдер на 429 отвечает 429 и на восьмой раз, а мы своими
        # руками усиливаем ту самую перегрузку, из-за которой он и отказал.
        try:
            r = _SUGGEST([{"role": h["role"], "text": h["text"]} for h in hist], known=known)
        except Exception as e:
            # ⚠ И В ЖУРНАЛ ТОЖЕ, С МЕСТОМ ПАДЕНИЯ (этап 5). До сих пор отказ мозга на
            # ЖИВЫХ аккаунтах Авито не оставлял в журнале ни строки: он жил только в
            # _LAST_ERRORS — строке панели, которую надо открыть глазами, да и та без
            # места падения. На мосту LeadChat место кладут в причину с 18.08; здесь
            # его не было, и разбор «почему бот молчал в чате» начинался с пустоты.
            # Пишем ОДИН раз на полосу отказов (не на каждый круг опроса раз в 12 с).
            _первый = "brain_fail_since" not in st
            # затяжной отказ: считаем от ПЕРВОГО падения и через 5 минут кричим
            # тем же устойчивым префиксом, на который настроено оповещение
            st.setdefault("brain_fail_since", now)
            if _первый:
                logbook.refused(logbook.причина(e), source="avito",
                                request_id=str(chat.get("id") or ""))
            if now - st["brain_fail_since"] > 300 and not st.get("brain_down_notified"):
                st["brain_down_notified"] = True
                print("[ЭСКАЛАЦИЯ-ПРОСРОЧКА] причина=мозг_недоступен чат=%s "
                      "клиент ждёт=%d мин" % (chat.get("id"),
                                              int((now - st["brain_fail_since"]) / 60)),
                      flush=True)
            raise  # прежний путь: наверх, в _LAST_ERRORS панели
        st.pop("brain_fail_since", None)
        st.pop("brain_down_notified", None)
        st["draft"] = (r.get("reply") or "").strip()
        st["draft_for_msg"] = last["id"]
        st["handoff"] = bool(r.get("handoff"))
        st["note"] = r.get("note") or ""
        fl = r.get("flag") or {}
        st["flag"] = fl.get("kind", "") if isinstance(fl, dict) else str(fl or "")
        # ⚠ 12.08: КОНТРАКТ РАЗВЁДЕН. Раньше смысл ехал на пустой строке и на проверке
        # «flag не равен оператору», которая была мёртвым кодом: метка ставилась только когда
        # у роутера нет своего kind, а тогда черновик и так пуст. Комментарий рядом врал —
        # строка `draft or ""` ничего не стирала. Теперь поля явные:
        #   reply_text   — что отправить (пусто = не отправлять),
        #   notify_human — звать ли человека,
        #   escalation   — почему, с причиной для очереди.
        st["notify_human"] = bool(r.get("notify_human") or r.get("to_operator")
                                  or r.get("pass_to_operator") or r.get("handoff"))
        esc = r.get("escalation") or {}
        st["escalation"] = esc.get("reason") or ""
        st["escalation_label"] = esc.get("label") or ""
        st["escalation_at"] = now if st["escalation"] else 0
        st["lead_ready"] = bool(r.get("lead_ready"))
        if st["notify_human"] and not st["flag"]:
            st["flag"] = "оператору"
    # автоответ: отправляем ровно то, что бот решил отправить. Человек зовётся отдельным
    # признаком и отправке не мешает — «текст клиенту + задача человеку» это одно состояние.
    if acc.get("auto_reply") and st.get("draft") and st.get("draft_for_msg") == last["id"] \
            and last["id"] not in st["sent_ids"]:
        avito_api.send(acc, chat.get("id"), st["draft"])
        st["sent_ids"] = (st["sent_ids"] + [last["id"]])[-50:]
        st["auto_sent"] += 1
        st["history"].append({"role": "bot", "text": st["draft"], "ts": now, "id": "auto"})
        st["unanswered"] = False
        avito_api.mark_read(acc, chat.get("id"))


def _overdue_watch(now):
    """Сторож очереди: эскалация, на которую человек не ответил дольше срока.

    ⚠ Очередь без присмотра — это то же молчание, которое мы лечим, только теперь с нашей
    санкции. Пишем в журнал строкой с устойчивым префиксом, по ней настраивается оповещение.
    Каждая просрочка сообщается ОДИН раз, чтобы не залить журнал."""
    import escalation as _esc
    for key, st in list(_STATE.items()):
        reason = st.get("escalation")
        at = st.get("escalation_at") or 0
        if not reason or not at or st.get("overdue_notified"):
            continue
        # снимает эскалацию только РУЧНОЙ ответ: автоответ бота тоже сбрасывает
        # unanswered, и сторож молчал всегда (аудит 16.08)
        if (st.get("last_manual_ts") or 0) > at:
            st["escalation"], st["escalation_at"] = "", 0
            st["overdue_notified"] = False
            continue
        limit = _esc.deadline_min(reason) * 60
        if now - at > limit:
            st["overdue_notified"] = True
            print("[ЭСКАЛАЦИЯ-ПРОСРОЧКА] причина=%s чат=%s ждёт=%d мин срок=%d мин"
                  % (reason, key, int((now - at) / 60), _esc.deadline_min(reason)), flush=True)


def _poll_once():
    now = time.time()
    try:
        _overdue_watch(now)
    except Exception:
        pass
    for acc in list(accounts()):
        if not acc.get("enabled"):
            continue
        try:
            ok = True
            for chat in avito_api.chats(acc, limit=30):
                try:
                    _process_chat(acc, chat, now)
                except avito_api.AvitoError as e:
                    ok = False
                    _LAST_ERRORS[acc.get("id")] = str(e)
                except Exception as e:            # мозг упал на одном чате — не роняем обход
                    ok = False
                    _LAST_ERRORS[acc.get("id")] = "чат %s: %s" % (chat.get("id"), e)
            if ok:
                _LAST_ERRORS.pop(acc.get("id"), None)
        except avito_api.AvitoError as e:
            _LAST_ERRORS[acc.get("id")] = str(e)
        except Exception as e:
            _LAST_ERRORS[acc.get("id")] = str(e)
    _persist_state()


def _loop():
    while True:
        try:
            _poll_once()
        except Exception:
            pass
        try:
            # сторож очереди заявок живёт здесь же: отдельная нить ради одной
            # дешёвой проверки раз в 12 секунд — расточительство (18.08)
            import server as _srv

            _srv._crm_queue_watch()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(POLL_SEC)


def start(suggest_fn, debounce_fn=None):
    """Запуск фонового опросчика. Зовёт server.main() ПОСЛЕ старта HTTP."""
    global _SUGGEST, _DEBOUNCE, _THREAD
    _SUGGEST = suggest_fn
    _DEBOUNCE = debounce_fn
    _restore_state()
    if _THREAD is None:
        _THREAD = threading.Thread(target=_loop, daemon=True, name="avito-channel")
        _THREAD.start()
    return _THREAD


# ---------- API для панели ----------

def overview():
    with _LOCK:
        accs = []
        for a in accounts():
            accs.append({"id": a.get("id"), "label": a.get("label"),
                         "client_id_mask": (a.get("client_id") or "")[:4] + "…",
                         "persona": a.get("persona"), "direction": a.get("direction"),
                         "partner": a.get("partner"), "white": a.get("white"),
                         "city": a.get("city"), "enabled": a.get("enabled"),
                         "auto_reply": a.get("auto_reply"),
                         "error": _LAST_ERRORS.get(a.get("id"), "")})
        chats = []
        for key, st in _STATE.items():
            acc_id = key.split(":", 1)[0]
            chats.append({"key": key, "account": acc_id, "title": st.get("title"),
                          "visitor": st.get("visitor"), "item_id": st.get("item_id"),
                          "updated": st.get("updated"), "unanswered": st.get("unanswered"),
                          "handoff": st.get("handoff"), "flag": st.get("flag"),
                          "auto_sent": st.get("auto_sent"), "resolved": st.get("resolved"),
                          "last": (st.get("history") or [{}])[-1].get("text", "")[:80]})
        chats.sort(key=lambda c: -(c.get("updated") or 0))
        # неразмеченные объявления (есть в реестре только с title)
        unmarked = {k: v for k, v in registry().items()
                    if not (v.get("direction") or v.get("partner") or v.get("city"))}
        return {"accounts": accs, "chats": chats, "registry": registry(),
                "unmarked": unmarked, "running": _THREAD is not None}


def chat_detail(key):
    with _LOCK:
        st = _STATE.get(key)
        if not st:
            return None
        return dict(st)


def send_reply(key, text):
    """Ручная отправка из панели (черновик или правленый оператором текст)."""
    acc_id, chat_id = key.split(":", 1)
    acc = next((a for a in accounts() if a.get("id") == acc_id), None)
    if not acc:
        raise avito_api.AvitoError("аккаунт не найден")
    avito_api.send(acc, chat_id, text)
    with _LOCK:
        st = _chat_state(key)
        st["history"].append({"role": "bot", "text": text, "ts": time.time(), "id": "manual"})
        st["unanswered"] = False
        st["last_manual_ts"] = time.time()   # ручной ответ — он и закрывает эскалации
        last = next((h for h in reversed(st["history"]) if h["role"] == "client"), None)
        if last:
            st["sent_ids"] = (st["sent_ids"] + [last.get("id")])[-50:]
        _persist_state()
    avito_api.mark_read(acc, chat_id)


def refresh_draft(key):
    """Пересчитать черновик по текущей истории (кнопка в панели)."""
    acc_id, chat_id = key.split(":", 1)
    acc = next((a for a in accounts() if a.get("id") == acc_id), None)
    with _LOCK:
        st = _STATE.get(key)
    if not (acc and st and st.get("history")):
        return None
    known = {"visitor": st.get("visitor"), "title": st.get("title"),
             "persona": acc.get("persona"), "direction": acc.get("direction"),
             "partner": acc.get("partner"), "white": acc.get("white"),
             "city": acc.get("city"), "widget_id": acc.get("id")}
    reg = registry().get(st.get("item_id") or "") or {}
    for k in ("direction", "partner", "white", "city", "persona"):
        if reg.get(k):
            known[k] = reg[k]
    r = _SUGGEST([{"role": h["role"], "text": h["text"]} for h in st["history"]], known=known)
    with _LOCK:
        st = _chat_state(key)
        st["draft"] = (r.get("reply") or "").strip()
        last = next((h for h in reversed(st["history"]) if h["role"] == "client"), None)
        st["draft_for_msg"] = (last or {}).get("id")
        st["handoff"] = bool(r.get("handoff"))
        st["note"] = r.get("note") or ""
        _persist_state()
        return dict(st)
