# -*- coding: utf-8 -*-
"""СТАТИСТИКА ДЛЯ ПАНЕЛИ (вкладка «Статистика», 04.08.2026).

Считает разрезы по ЖИВОМУ потоку Jivo-приёмника (paths.DIALOGS + dialogs.jsonl +
исторический paths.MERGED) и добавляет текущее состояние своего канала Avito
(channel.overview() передаёт server). Ничего не пишет, только читает; скан
кэшируется на TTL секунд, при неизменном наборе файлов пересчёта нет.

Разрезы под вопросы заказчика:
  • сколько в работе и у кого (операторы, аккаунты-виджеты);
  • по каким городам пишут (город из URL объявления, avito_city);
  • с какого источника (метка Jivo: партнёр/белый/код источника, jivo_meta);
  • какие аккаунты неактивные или в просадке (7 дней против предыдущих 7).
"""
import glob
import json
import os
import statistics
import threading
import time

import avito_city
import jivo_meta
import paths

TTL = 60                       # сек: чаще панель дёргать смысла нет
SILENT_DAYS = 3                # виджет «молчит», если тишина дольше этого
DROP_RATIO = 0.6               # «просадка»: неделя < 60% предыдущей (и та была не пустой)
DROP_MIN_PREV = 5              # ...при этом в прошлой неделе было хотя бы столько диалогов

_LOCK = threading.Lock()
_CACHE = {"key": None, "built": 0, "data": None}


# ---------- чтение диалогов ----------

def _iter_records(scan_from):
    """Все завершённые диалоги с ts >= scan_from, дедуп по conversation_key.
    Источники: свежие куски приёмника (DIALOGS), dialogs.jsonl рядом с ними,
    исторический корпус MERGED (если смонтирован)."""
    seen = set()

    def _emit(d):
        key = d.get("conversation_key") or ""
        if not key or key in seen:
            return None
        seen.add(key)
        return d

    jsonl = os.path.join(os.path.dirname(paths.DIALOGS), "dialogs.jsonl")
    if os.path.isfile(jsonl):
        try:
            with open(jsonl, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if _first_ts(d) >= scan_from and _emit(d):
                        yield d
        except OSError:
            pass
    for folder in (paths.DIALOGS, paths.MERGED):
        if not os.path.isdir(folder):
            continue
        for fp in glob.glob(os.path.join(folder, "*.json")):
            try:
                if os.path.getmtime(fp) < scan_from - 86400:
                    continue                       # файл заведомо старше окна
                d = json.load(open(fp, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if _first_ts(d) >= scan_from and _emit(d):
                yield d


def _first_ts(d):
    for m in d.get("messages") or []:
        try:
            return float(m.get("ts"))
        except (TypeError, ValueError):
            continue
    return 0.0


def _files_fingerprint():
    """Дешёвый отпечаток источников: кэш не пересобираем, пока ничего не менялось."""
    parts = []
    jsonl = os.path.join(os.path.dirname(paths.DIALOGS), "dialogs.jsonl")
    for p in (jsonl, paths.DIALOGS, paths.MERGED):
        try:
            st = os.stat(p)
            parts.append("%s:%s:%s" % (p, st.st_mtime, getattr(st, "st_size", 0)))
        except OSError:
            parts.append(p + ":нет")
    return "|".join(parts)


# ---------- агрегация ----------

def _day(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _bump(row, ts, now):
    """Инкремент счётчиков строки-разреза по попаданию диалога в окна."""
    age = now - ts
    row["total"] += 1
    if _day(ts) == _day(now):
        row["today"] += 1
    if age < 7 * 86400:
        row["d7"] += 1
    elif age < 14 * 86400:
        row["prev7"] += 1
    if ts > row["last_ts"]:
        row["last_ts"] = ts


def _new_row(**extra):
    row = {"total": 0, "today": 0, "d7": 0, "prev7": 0, "last_ts": 0}
    row.update(extra)
    return row


def _trend(d7, prev7):
    if prev7 <= 0:
        return None if d7 == 0 else 100
    return int(round((d7 - prev7) * 100.0 / prev7))


def _widget_status(row, now):
    if row["last_ts"] and (now - row["last_ts"]) > SILENT_DAYS * 86400:
        return "silent"
    if row["prev7"] >= DROP_MIN_PREV and row["d7"] < row["prev7"] * DROP_RATIO:
        return "drop"
    return "ok"


def _collect(days, now):
    scan_from = now - max(days, 14) * 86400        # тренды всегда хотят 2 недели
    by_day = {}
    widgets, agents, cities, sources = {}, {}, {}, {}
    total = _new_row()
    reply_secs_all = []

    for d in _iter_records(scan_from):
        meta = d.get("meta") or {}
        msgs = d.get("messages") or []
        ts0 = _first_ts(d)
        if not ts0:
            continue
        _bump(total, ts0, now)
        day = _day(ts0)
        by_day[day] = by_day.get(day, 0) + 1

        page = meta.get("page") or {}
        label = jivo_meta.from_pages([page]) or {}
        city = avito_city.city_from_url(page.get("url") or "") or ""

        # --- виджет (аккаунт Avito, подключённый через Jivo) ---
        wid = meta.get("widget_id") or "?"
        w = widgets.get(wid)
        if w is None:
            w = widgets[wid] = _new_row(id=wid, cities={}, dirs={}, source="", partner="",
                                        white=False, reply_secs=[])
        _bump(w, ts0, now)
        if city:
            w["cities"][city] = w["cities"].get(city, 0) + 1
        if label.get("direction"):
            w["dirs"][label["direction"]] = w["dirs"].get(label["direction"], 0) + 1
        if label.get("source") and not w["source"]:
            w["source"] = label["source"]
        if label.get("partner") and not w["partner"]:
            w["partner"] = label["partner"]
        w["white"] = w["white"] or bool(label.get("white"))

        # --- город и источник ---
        if city:
            c = cities.get(city) or cities.setdefault(city, _new_row(city=city))
            _bump(c, ts0, now)
        pkey = ("парт %s%s" % (label.get("partner"), " БЕЛЫЙ" if label.get("white") else "")) \
            if label.get("partner") else "без метки"
        s = sources.get(pkey) or sources.setdefault(pkey, _new_row(source=pkey))
        _bump(s, ts0, now)

        # --- операторы и скорость первого ответа ---
        names = {a.get("id"): (a.get("name") or "").strip()
                 for a in (meta.get("agents") or []) if isinstance(a, dict)}
        first_client, first_agent, agent_of_first = None, None, None
        replied = set()
        for m in msgs:
            try:
                ts = float(m.get("ts"))
            except (TypeError, ValueError):
                continue
            if m.get("role") == "client":
                if first_client is None:
                    first_client = ts
            elif m.get("agent_id"):
                aid = m.get("agent_id")
                replied.add(aid)
                if first_agent is None and first_client is not None:
                    first_agent, agent_of_first = ts, aid
                a = agents.get(aid)
                if a is None:
                    a = agents[aid] = _new_row(id=aid, name="", replies=0, reply_secs=[])
                a["replies"] += 1
                if ts > a["last_ts"]:
                    a["last_ts"] = ts
        for aid in replied:
            a = agents[aid]
            a["total"] += 1
            if _day(ts0) == _day(now):
                a["today"] += 1
            if now - ts0 < 7 * 86400:
                a["d7"] += 1
            elif now - ts0 < 14 * 86400:
                a["prev7"] += 1
            if not a["name"] and names.get(aid):
                a["name"] = names[aid]
        if first_client is not None and first_agent is not None and first_agent >= first_client:
            sec = first_agent - first_client
            if sec < 6 * 3600:                     # ночные «ответы утром» медиану не портят
                reply_secs_all.append(sec)
                w["reply_secs"].append(sec)
                if agent_of_first in agents:
                    agents[agent_of_first]["reply_secs"].append(sec)

    return by_day, widgets, agents, cities, sources, total, reply_secs_all


def _median(xs):
    return int(statistics.median(xs)) if xs else None


def _pack(days, now):
    by_day, widgets, agents, cities, sources, total, reply_all = _collect(days, now)

    days_list = []
    for i in range(days - 1, -1, -1):
        day = _day(now - i * 86400)
        days_list.append({"day": day[5:], "n": by_day.get(day, 0)})

    def top_dir(w):
        return max(w["dirs"], key=w["dirs"].get) if w["dirs"] else ""

    def top_city(w):
        return max(w["cities"], key=w["cities"].get) if w["cities"] else ""

    ws = []
    for w in widgets.values():
        ws.append({
            "id": w["id"], "source": w["source"], "partner": w["partner"],
            "white": w["white"], "direction": top_dir(w), "city": top_city(w),
            "today": w["today"], "d7": w["d7"], "prev7": w["prev7"],
            "trend": _trend(w["d7"], w["prev7"]), "last_ts": w["last_ts"],
            "median_reply": _median(w["reply_secs"]),
            "status": _widget_status(w, now),
        })
    ws.sort(key=lambda x: (-x["d7"], -x["today"]))

    ags = []
    for a in agents.values():
        ags.append({
            "id": a["id"], "name": a["name"] or ("оператор %s" % a["id"]),
            "today": a["today"], "d7": a["d7"], "prev7": a["prev7"],
            "replies": a["replies"], "trend": _trend(a["d7"], a["prev7"]),
            "median_reply": _median(a["reply_secs"]), "last_ts": a["last_ts"],
        })
    ags.sort(key=lambda x: -x["d7"])

    cs = sorted(cities.values(), key=lambda c: -c["d7"])
    cs = [{"city": c["city"], "today": c["today"], "d7": c["d7"], "prev7": c["prev7"],
           "trend": _trend(c["d7"], c["prev7"])} for c in cs[:30]]

    srcs = sorted(sources.values(), key=lambda s: -s["d7"])
    srcs = [{"source": s["source"], "today": s["today"], "d7": s["d7"], "prev7": s["prev7"],
             "trend": _trend(s["d7"], s["prev7"])} for s in srcs]

    silent = sum(1 for w in ws if w["status"] == "silent")
    drop = sum(1 for w in ws if w["status"] == "drop")
    return {
        "days": days,
        "built": int(now),
        "totals": {
            "today": total["today"], "d7": total["d7"], "prev7": total["prev7"],
            "trend": _trend(total["d7"], total["prev7"]),
            "widgets": len(ws), "widgets_silent": silent, "widgets_drop": drop,
            "median_reply": _median(reply_all),
        },
        "by_day": days_list,
        "widgets": ws,
        "agents": ags,
        "cities": cs,
        "sources": srcs,
    }


# ---------- публичный вход ----------

def overview(days=14, channel_data=None):
    """JSON для /api/channel/stats. channel_data — готовый channel.overview()."""
    days = max(1, min(int(days or 14), 60))
    now = time.time()
    key = "%d|%s" % (days, _files_fingerprint())
    with _LOCK:
        if _CACHE["data"] is not None and _CACHE["key"] == key \
                and now - _CACHE["built"] < TTL:
            data = _CACHE["data"]
        else:
            data = _pack(days, now)
            _CACHE.update(key=key, built=now, data=data)
    out = dict(data)
    ch = {"accounts": 0, "enabled": 0, "auto": 0, "chats": 0, "waiting": 0,
          "handoff": 0, "auto_sent": 0, "errors": 0}
    if channel_data:
        accs = channel_data.get("accounts") or []
        chats = channel_data.get("chats") or []
        ch = {
            "accounts": len(accs),
            "enabled": sum(1 for a in accs if a.get("enabled")),
            "auto": sum(1 for a in accs if a.get("auto_reply")),
            "errors": sum(1 for a in accs if a.get("error")),
            "chats": len(chats),
            "waiting": sum(1 for c in chats if c.get("unanswered")),
            "handoff": sum(1 for c in chats if c.get("handoff")),
            "auto_sent": sum(int(c.get("auto_sent") or 0) for c in chats),
        }
    out["channel"] = ch
    return out
