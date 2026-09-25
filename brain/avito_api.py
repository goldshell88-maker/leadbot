# -*- coding: utf-8 -*-
"""Клиент Messenger API Avito для СВОЕЙ панели бота (без Jivo и без Чат Хаба).

Аккаунт = client_id + client_secret из кабинета Avito (avito.ru/professionals/api).
Токен OAuth2 client_credentials живёт ~24 часа — кэшируем в памяти с запасом.

⚠ Эндпоинты Messenger API по документации Avito на 2026 год; если Avito сменит версию,
править ТОЛЬКО здесь — панель и канал ходят через эти функции. Все ошибки заворачиваются
в AvitoError с человекочитаемым текстом — панель показывает их оператору как есть.
"""
import json
import time
import urllib.request
import urllib.error
import urllib.parse

BASE = "https://api.avito.ru"
_TOKENS = {}     # client_id -> {"token": str, "exp": epoch}
_SELF_IDS = {}   # client_id -> user_id


class AvitoError(Exception):
    pass


def _req(method, url, data=None, headers=None, timeout=20):
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise AvitoError("Avito HTTP %s на %s: %s" % (e.code, url.split("?")[0], detail or e.reason))
    except urllib.error.URLError as e:
        raise AvitoError("Avito недоступен (%s): %s" % (url.split("?")[0], e.reason))


def token(acc):
    """OAuth-токен по client_credentials; кэш в памяти с запасом 10 минут."""
    cid = (acc.get("client_id") or "").strip()
    sec = (acc.get("client_secret") or "").strip()
    if not cid or not sec:
        raise AvitoError("у аккаунта не заполнены client_id/client_secret")
    cached = _TOKENS.get(cid)
    if cached and cached["exp"] > time.time():
        return cached["token"]
    form = urllib.parse.urlencode({
        "grant_type": "client_credentials", "client_id": cid, "client_secret": sec}).encode()
    req = urllib.request.Request(BASE + "/token", data=form, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            out = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        raise AvitoError("Avito не дал токен (HTTP %s): %s — проверьте client_id/client_secret"
                         % (e.code, detail))
    except urllib.error.URLError as e:
        raise AvitoError("Avito недоступен (/token): %s" % e.reason)
    tok = out.get("access_token")
    if not tok:
        raise AvitoError("Avito ответил без access_token: %s" % json.dumps(out)[:200])
    _TOKENS[cid] = {"token": tok, "exp": time.time() + int(out.get("expires_in", 86400)) - 600}
    return tok


def _auth(acc):
    return {"Authorization": "Bearer " + token(acc), "Content-Type": "application/json"}


def self_id(acc):
    """user_id аккаунта — нужен во всех messenger-путях."""
    cid = (acc.get("client_id") or "").strip()
    if cid in _SELF_IDS:
        return _SELF_IDS[cid]
    out = _req("GET", BASE + "/core/v1/accounts/self", headers=_auth(acc))
    uid = out.get("id")
    if not uid:
        raise AvitoError("не удалось получить id аккаунта: %s" % json.dumps(out)[:200])
    _SELF_IDS[cid] = uid
    return uid


def chats(acc, unread_only=False, limit=30):
    """Список чатов аккаунта (свежие сверху). item-чаты = по объявлениям."""
    uid = self_id(acc)
    q = "?limit=%d&chat_types=u2i" % limit + ("&unread_only=true" if unread_only else "")
    out = _req("GET", BASE + "/messenger/v2/accounts/%s/chats%s" % (uid, q), headers=_auth(acc))
    return out.get("chats") or []


def messages(acc, chat_id, limit=50):
    """Сообщения чата, СТАРЫЕ ПЕРВЫМИ (API отдаёт новые первыми — разворачиваем)."""
    uid = self_id(acc)
    out = _req("GET", BASE + "/messenger/v3/accounts/%s/chats/%s/messages/?limit=%d"
               % (uid, urllib.parse.quote(str(chat_id)), limit), headers=_auth(acc))
    msgs = out.get("messages") or (out if isinstance(out, list) else [])
    msgs = sorted(msgs, key=lambda m: m.get("created") or 0)
    return msgs


def send(acc, chat_id, text):
    """Отправка текстового сообщения от лица аккаунта."""
    uid = self_id(acc)
    return _req("POST", BASE + "/messenger/v1/accounts/%s/chats/%s/messages"
                % (uid, urllib.parse.quote(str(chat_id))),
                data={"message": {"text": text}, "type": "text"}, headers=_auth(acc))


def mark_read(acc, chat_id):
    try:
        uid = self_id(acc)
        _req("POST", BASE + "/messenger/v1/accounts/%s/chats/%s/read"
             % (uid, urllib.parse.quote(str(chat_id))), data={}, headers=_auth(acc))
    except AvitoError:
        pass  # непрочитанность — косметика, не роняем цикл


def check(acc):
    """Проверка связи для панели: токен + self id. Возвращает (ok, текст)."""
    try:
        uid = self_id(acc)
        return True, "связь есть, user_id %s" % uid
    except AvitoError as e:
        return False, str(e)
