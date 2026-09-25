# -*- coding: utf-8 -*-
"""Живой ридер диалогов из запущенного Jivo-вебхука — БЕЗ вмешательства в него.

Открытые (незавершённые) чаты собираются повторным проигрыванием сырья
(`data/raw/<дата>.jsonl`) через РОДНОЙ парсер вебхука (его server.py импортируется
как модуль) в отдельный временный стор. Завершённые диалоги читаются из
`data/dialogs/*.json`. Ничего в рабочем приёмнике не меняется и не пишется.
"""
import os, glob, json, time, hashlib, tempfile, shutil, contextlib, importlib.util

import claude_api
import avito_city
import prefilter
import paths
import jivo_meta
import multi_account

# ---- Индекс дублей (по тексту и телефону) для пре-фильтра «слива» ------------
_DUP_INDEX = None
_DUP_TS = 0


def dup_index(max_age=120):
    """{texts,phones,text_keys,phone_keys} по всем завершённым диалогам. Кэш 2 мин."""
    global _DUP_INDEX, _DUP_TS
    now = time.time()
    if _DUP_INDEX is not None and (now - _DUP_TS) < max_age:
        return _DUP_INDEX
    text_groups, phone_groups = {}, {}
    records = []
    jsonl = os.path.join(data_dir(), "dialogs.jsonl")
    if os.path.exists(jsonl):
        try:
            with open(jsonl, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except ValueError:
                            pass
        except Exception:
            pass
    else:
        for fp in glob.glob(os.path.join(data_dir(), "dialogs", "*.json")):
            try:
                records.append(json.load(open(fp, encoding="utf-8")))
            except Exception:
                pass
    for d in records:
        key = d.get("conversation_key")
        client = [m for m in (d.get("messages") or []) if m.get("role") == "client"]
        if not client:
            continue
        ts0 = 0
        for m in client:
            try:
                ts0 = float(m.get("ts"))
                break
            except (TypeError, ValueError):
                continue
        nt = prefilter.norm_text(client[0].get("text", ""))
        if len(nt) >= 25:
            text_groups.setdefault(nt, []).append((ts0, key))
        for ph in prefilter.phones_in(" \n".join(m.get("text", "") for m in client)):
            phone_groups.setdefault(ph, []).append((ts0, key))
    for g in text_groups.values():
        g.sort()
    for g in phone_groups.values():
        g.sort()
    _DUP_INDEX = {"text_groups": text_groups, "phone_groups": phone_groups}
    _DUP_TS = now
    return _DUP_INDEX


def _first_client_ts(msgs):
    for m in msgs:
        if m.get("role") == "client":
            try:
                return float(m.get("ts"))
            except (TypeError, ValueError):
                return 0
    return 0

_DIR_KEYS = {
    "mnc": ["мебел", "кухн", "сборк", "сантех", "электрик", "муж на час", "мелкий",
             "плинтус", "карниз", "навес", "столешн", "двер", "ремонт квартир"],
    "bt":  ["стиральн", "холодильн", "посудомоеч", "плита", "духов", "варочн", "телевизор",
             "бытов", "микроволнов", "кондицион", "водонагрев", "тв "],
    "kp":  ["компьютер", "ноутбук", " пк", "windows", "интернет", "роутер", "принтер",
             "программ", "восстановление данных", "приставк"],
}


def _dir_from_title(title):
    t = (title or "").lower()
    for cat, keys in _DIR_KEYS.items():
        if any(k in t for k in keys):
            return cat
    return "unknown"

_MOD = None


def webhook_dir():
    """Путь к приёмнику. Раньше тут был вшит абсолютный windows-путь — теперь ищем
    через paths (env → config → соседняя папка), чтобы проект переезжал на любую машину."""
    cfg = claude_api.config().get("jivo_webhook_dir")
    if cfg and os.path.isdir(cfg):
        return cfg
    return paths.webhook_dir()


def data_dir():
    return os.path.join(webhook_dir(), "data")


def _load_webhook_module():
    """Импортируем server.py вебхука под своим именем (main() не запускается)."""
    global _MOD
    if _MOD is not None:
        return _MOD
    path = os.path.join(webhook_dir(), "server.py")
    spec = importlib.util.spec_from_file_location("jivo_webhook_server", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _MOD = mod
    return mod


def _norm_messages(msgs):
    out = []
    for m in msgs:
        text = m.get("text")
        if text is None and m.get("type"):
            text = "<%s>" % m.get("type")
        out.append({
            "role": m.get("role"),
            "text": text or "",
            "time": m.get("time") or "",
            "ts": m.get("ts"),
        })
    return out


def _persona_for(widget_id):
    """Персона аккаунта: стабильно выводится из widget_id (один виджет = один аккаунт Avito).

    ⚠ НАЙДЕНО ЗАМЕРОМ 01.08.2026: в живом канале персона не задавалась ВООБЩЕ — _meta_brief её
    не возвращал, и в known она приходила пустой. Десять персон работали только в Пульте, где
    оператор выставляет аккаунт руками, а на экране оператора все аккаунты говорили одним
    голосом. Для Avito это прямой риск: одинаковый стиль на разных профилях — то, за что их
    понижают, и ровно от этого мы защищаемся персонами.
    Привязка к widget_id, а не к номеру диалога: у аккаунта должен быть ПОСТОЯННЫЙ голос,
    иначе один и тот же клиент в одном чате увидит смену манеры.
    """
    wid = str(widget_id or "").strip()
    if not wid:
        return ""
    n = int(hashlib.sha256(wid.encode("utf-8")).hexdigest(), 16) % 10 + 1
    return "p%d" % n


def _meta_brief(meta):
    v = meta.get("visitor") or meta.get("client") or {}
    page = meta.get("page") or {}
    social = ((v.get("social") or {}).get("socialProfiles") or []) if isinstance(v, dict) else []
    src = "avito" if (social and social[0].get("typeName") == "av") else (
        "avito" if "avito" in str(page.get("url", "")) else "jivo")
    title = page.get("title") or ""
    if title.strip().lower() in ("чат с клиентом",):
        title = ""
    # СЛУЖЕБНАЯ МЕТКА JIVO («! Парт - 723 БЕЛЫЙ / Ист - Б3 МНЧ !») приходит как одна из
    # «страниц» визита. В ней партнёр (от него зависят цены) и направление, проставленное
    # заказчиком вручную, — оно надёжнее, чем угадывание по заголовку объявления.
    label = jivo_meta.from_pages([page.get("url", ""), title])
    return {
        "visitor": (v.get("name") if isinstance(v, dict) else "") or "",
        # ХЕШ ПРОФИЛЯ AVITO — один и тот же на ВСЕХ наших аккаунтах, в отличие от имени и от
        # visitor.number (тот у Jivo свой на каждый виджет). Нужен для правила «человек писал
        # больше чем на 5 аккаунтов → 6-й и далее сливаем».
        "avito_uid": multi_account.avito_uid(meta),
        "widget_id": str(meta.get("widget_id") or meta.get("site_id") or ""),
        # ПЕРСОНА АККАУНТА — без неё все наши профили Avito говорят одним голосом (см. _persona_for)
        "persona": _persona_for(meta.get("widget_id") or meta.get("site_id")),
        "title": title,
        "source": src,
        "city": avito_city.city_from_url(page.get("url", "")),   # город из ссылки Avito
        "partner": (label or {}).get("partner") or "",
        "white": bool((label or {}).get("white")),
        "src_code": (label or {}).get("source") or "",
        # направление: сперва из метки заказчика, иначе по заголовку объявления
        "direction": (label or {}).get("direction") or _dir_from_title(title),
        "agents": [a.get("name") for a in (meta.get("agents") or []) if isinstance(a, dict)],
    }


def open_dialogs(max_age_min=180):
    """Открытые чаты: проигрываем сырьё за 2 дня в свежий временный стор.

    Запись готовых диалогов на диск отключаем (нам нужны только ОТКРЫТЫЕ,
    оставшиеся в store.chats) — иначе на каждый chat_finished родной парсер
    писал бы файл и печатал в консоль.
    """
    mod = _load_webhook_module()
    tmp = tempfile.mkdtemp(prefix="jivo_live_")
    try:
        store = mod.DialogStore(tmp)
        store._write_dialog = lambda *a, **k: None   # не пишем готовые на диск
        store.save_offline = lambda *a, **k: None
        raw_files = sorted(glob.glob(os.path.join(data_dir(), "raw", "*.jsonl")))[-2:]
        with open(os.devnull, "w") as dn, contextlib.redirect_stdout(dn):
            for rf in raw_files:
                try:
                    with open(rf, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                entry = json.loads(line)
                            except ValueError:
                                continue
                            payload = entry.get("payload")
                            items = payload if isinstance(payload, list) else [payload]
                            for it in items:
                                if isinstance(it, dict):
                                    try:
                                        mod.process_payload(store, it)
                                    except Exception:
                                        pass
                except Exception:
                    pass
        chats = list(store.chats.items())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    now = time.time()
    result = []
    for key, chat in chats:
        msgs = chat.get("messages") or []
        if not msgs:
            continue
        last_ts = chat.get("last_ts") or 0
        try:
            last_ts = float(last_ts)
        except (TypeError, ValueError):
            last_ts = 0
        if last_ts and (now - last_ts) > max_age_min * 60:
            continue  # давно затих — вебхук его уже сохранил, не показываем как «живой»
        brief = _meta_brief(chat.get("meta") or {})
        result.append({
            "key": key, "status": "open",
            "updated": chat.get("last_ts"),
            "n": len(msgs), "last_role": msgs[-1].get("role"),
            "messages": _norm_messages(msgs), **brief,
        })
    result.sort(key=lambda d: d.get("updated") or 0, reverse=True)
    return result


def finished_dialogs(limit=40):
    files = sorted(glob.glob(os.path.join(data_dir(), "dialogs", "*.json")), reverse=True)[:limit]
    out = []
    for fp in files:
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        msgs = _norm_messages(d.get("messages") or [])
        if not msgs:
            continue
        brief = _meta_brief(d.get("meta") or {})
        out.append({
            "key": d.get("conversation_key"), "status": "finished",
            "updated": d.get("saved_at"), "file": os.path.basename(fp),
            "n": len(msgs), "last_role": msgs[-1].get("role") if msgs else None,
            "messages": msgs, **brief,
        })
    return out


def list_dialogs(finished_limit=40):
    """Открытые + недавние завершённые, открытые сверху, без дублей по ключу."""
    opened = open_dialogs()
    seen = {d["key"] for d in opened}
    fin = [d for d in finished_dialogs(finished_limit) if d["key"] not in seen]
    return opened + fin


def summaries(finished_limit=40):
    idx = dup_index()
    engage_n = int(claude_api.config().get("engage_limit", 5))
    out = []
    for d in list_dialogs(finished_limit):
        msgs = d.get("messages") or []
        last = msgs[-1]["text"] if msgs else ""
        row = {k: d[k] for k in ("key", "status", "updated", "n", "last_role",
                                 "visitor", "title", "source", "city", "direction")}
        row["preview"] = (last[:80] + "…") if len(last) > 80 else last
        fl = prefilter.classify(msgs, idx, self_key=d["key"],
                                self_ts=_first_client_ts(msgs), engage_n=engage_n)
        row["flag"] = {"kind": fl["kind"], "sub": fl.get("sub"), "reason": fl["reason"]} if fl else None
        out.append(row)
    return out


def get_dialog(key):
    engage_n = int(claude_api.config().get("engage_limit", 5))
    for d in list_dialogs(finished_limit=200):
        if d["key"] == key:
            msgs = d.get("messages") or []
            d["flag"] = prefilter.classify(msgs, dup_index(), self_key=key,
                                           self_ts=_first_client_ts(msgs), engage_n=engage_n)
            return d
    return None
