# -*- coding: utf-8 -*-
"""ОДИН ЧЕЛОВЕК НА МНОГИХ НАШИХ АККАУНТАХ (правило «Правила Авито» + заказчик 01.08.2026).

Дословно: «Не всегда пишет один человек, и если человек написал больше чем на 5 аккаунтов,
то 6-й и последующие диалоги можно сливать». То же в канале «Правила Авито»: до 5 аккаунтов
включительно ОТКАЗЫВАТЬ ЗАПРЕЩЕНО (пока не получен номер), с 6-го — можно.

ЧЕМ ОПОЗНАЁМ ЧЕЛОВЕКА. Не именем (Ирин и Александров сотни) и не visitor.number — он у Jivo
свой на каждый виджет. Стабилен ХЕШ ПРОФИЛЯ AVITO: он приходит в вебхуке в
visitor.social.socialProfiles[].url вида
    https://avito.ru/user/0123456789ab…/profile?iid=…
и у одного человека одинаков на всех наших аккаунтах. Проверено по 37 470 сырых вебхуков:
3534 уникальных профиля, из них 10 писали больше чем на 5 аккаунтов (рекорд — 14).

СЧЁТ ВЕДЁМ ПО НАШИМ АККАУНТАМ (widget_id), а не по числу диалогов: пять переписок на одном
аккаунте — это обычный вернувшийся клиент, его сливать нельзя.
"""
import json
import os
import re
import threading
import time

import paths

_UID_RX = re.compile(r"avito\.ru/user/([0-9a-f]{16,40})", re.IGNORECASE)
_STORE = os.path.join(paths.ROOT, "data", "multi_account.json")
_LOCK = threading.Lock()
_MEM = None
LIMIT = 5                      # до 5 аккаунтов включительно сливать НЕЛЬЗЯ
_TTL_DAYS = 90                 # старше — забываем, человек мог вернуться по новой задаче


def avito_uid(meta):
    """Стабильный идентификатор профиля Avito из мета-данных вебхука ('' если нет)."""
    v = (meta or {}).get("visitor") or (meta or {}).get("client") or {}
    if not isinstance(v, dict):
        return ""
    for p in ((v.get("social") or {}).get("socialProfiles") or []):
        m = _UID_RX.search(str((p or {}).get("url") or ""))
        if m:
            return m.group(1).lower()
    return ""


def _load():
    global _MEM
    if _MEM is not None:
        return _MEM
    try:
        with open(_STORE, encoding="utf-8") as f:
            _MEM = json.load(f)
    except Exception:
        _MEM = {}
    return _MEM


def _save():
    try:
        os.makedirs(os.path.dirname(_STORE), exist_ok=True)
        tmp = _STORE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_MEM, f, ensure_ascii=False)
        os.replace(tmp, _STORE)
    except Exception:
        pass


def seen(uid, widget_id):
    """Запоминает, что профиль uid писал на аккаунт widget_id. Возвращает число аккаунтов."""
    if not uid or not widget_id:
        return 0
    now = int(time.time())
    with _LOCK:
        mem = _load()
        rec = mem.get(uid) or {"w": {}, "t": now}
        rec["w"][str(widget_id)] = now
        cutoff = now - _TTL_DAYS * 86400
        rec["w"] = {k: v for k, v in rec["w"].items() if v >= cutoff}
        rec["t"] = now
        mem[uid] = rec
        if len(mem) > 20000:                      # не даём файлу расти бесконечно
            for k in sorted(mem, key=lambda x: mem[x].get("t", 0))[:5000]:
                mem.pop(k, None)
        _save()
        return len(rec["w"])


def check_multi_account(meta, has_phone=False):
    """Слив 6-го и последующих аккаунтов одного человека (0 токенов).

    has_phone — номер клиента уже есть в переписке. Правило «Правил Авито»: пока номера нет,
    отказывать нельзя вообще; порог работает только когда мы уже ничего не теряем.
    Возвращает dict как у прочих роутеров либо None.
    """
    m = meta or {}
    # принимаем и сырой вебхук, и уже разобранную краткую форму jivo_live._meta_brief
    uid = m.get("avito_uid") or avito_uid(m)
    wid = m.get("widget_id") or m.get("site_id") or ""
    if not uid or not wid:
        return None
    n = seen(uid, wid)
    if n <= LIMIT:
        return None
    return {"kind": "multi_account", "accounts": n,
            "reason": "один профиль Avito пишет на %d наших аккаунта — 6-й и далее сливаем "
                      "(правило «Правил Авито»)" % n,
            "silent": not has_phone}
