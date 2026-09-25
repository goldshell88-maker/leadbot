# -*- coding: utf-8 -*-
"""ОБЪЕДИНИТЕЛЬ РАЗБИТЫХ ДИАЛОГОВ (инкрементальный, с очисткой исходников).

Jivo создаёт НОВЫЙ chat_id при переоткрытии чата, поэтому один Avito-разговор
(клиент × объявление) разлетается на несколько файлов. Схема работы:

  data/dialogs*  (сырые куски от приёмника)  ──втягиваются──►  data/dialogs_merged  (ПОСТОЯННОЕ хранилище)
                                                └── после успешного втягивания исходники УДАЛЯЮТСЯ

- dialogs_merged — главный полный корпус (1 файл = 1 полный диалог клиент×объявление).
- Ключ диалога: (visitor.number, page.title); склейка частей по saved_at со срезкой
  перекрытий (суффикс==префикс по (role,text)) — повторное втягивание того же куска
  ничего не дублирует (идемпотентно).
- Файлы моложе 2 минут не трогаем (могут дописываться приёмником прямо сейчас).
- Запись атомарная (tmp → replace), исходник удаляется ТОЛЬКО после успешной записи.
- Опустевшие архивные папки dialogs_old_* удаляются (живую dialogs не трогаем).

Запуск:  py analysis\\merge_dialogs.py   (или задача планировщика «LeadBot Merge Dialogs», ежечасно)
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import paths  # noqa: E402  — единая точка правды по путям (переносимо между машинами)
import sys, io, os, glob, json, re, time, collections

if sys.stdout is not None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _p(msg):
    if sys.stdout is not None:
        print(msg)


DATA = paths.DATA
OUT = os.path.join(DATA, "dialogs_merged")
SAFE_AGE_SEC = 120


def visitor_key(d):
    v = ((d.get("meta") or {}).get("visitor") or {})
    return v.get("number") or v.get("name")


def page_key(d):
    m = d.get("meta") or {}
    t = ((m.get("page") or {}).get("title") or "").strip()
    return t if t else ("widget:" + str(m.get("widget_id") or ""))


def group_key(d):
    vk = visitor_key(d)
    return (vk, page_key(d)) if vk else None


def msg_sig(m):
    return (m.get("role"), (m.get("text") or "").strip())


def merge_messages(base, new):
    """Приклеивает new к base. Идемпотентно: если кусок УЖЕ содержится в base целиком
    (непрерывной подпоследовательностью) — ничего не добавляем; иначе срезаем
    перекрытие на стыке (суффикс base == префикс new)."""
    if not base:
        return list(new)
    if not new:
        return base
    sb = [msg_sig(m) for m in base]
    sn = [msg_sig(m) for m in new]
    n = len(sn)
    if any(sb[i:i + n] == sn for i in range(len(sb) - n + 1)):
        return base                                # кусок уже внутри — повторное втягивание
    overlap = 0
    for k in range(min(len(sb), n), 0, -1):
        if sb[-k:] == sn[:k]:
            overlap = k
            break
    return base + new[overlap:]


def rebuild_from_jsonl():
    """АВАРИЙНАЯ ПЕРЕСБОРКА: собирает dialogs_merged С НУЛЯ из полных jsonl-логов
    приёмника (data/dialogs.jsonl*) — там есть каждый кусок каждого диалога.
    Запуск: py merge_dialogs.py --rebuild"""
    import shutil
    parts = []
    for path in sorted(glob.glob(os.path.join(DATA, "dialogs.jsonl*"))):
        if os.path.isdir(path):
            continue
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("messages") is not None:
                parts.append((d.get("saved_at") or "", d))
    parts.sort(key=lambda x: x[0])
    _p("Пересборка из jsonl: кусков %d" % len(parts))

    groups = {}
    order = {}
    for _, d in parts:
        gk = group_key(d) or ("_nokey_", d.get("conversation_key"))
        if gk not in groups:
            nd = dict(d)
            nd["merged_from"] = []
            groups[gk] = nd
            order[gk] = len(order)
        else:
            nd = groups[gk]
            nd["messages"] = merge_messages(nd.get("messages") or [], d.get("messages") or [])
            for f in ("saved_at", "finish_reason", "meta"):
                if d.get(f):
                    nd[f] = d[f]
            ck_old = nd.get("conversation_key") or ""
            ck_new = d.get("conversation_key") or ""
            if ck_new and ck_new not in ck_old.split("+"):
                nd["conversation_key"] = (ck_old + "+" + ck_new) if ck_old else ck_new
        groups[gk]["merged_from"].append({"key": d.get("conversation_key"),
                                          "msgs": len(d.get("messages") or [])})
        groups[gk]["message_count"] = len(groups[gk].get("messages") or [])

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    for gk, nd in groups.items():
        first_key = (nd.get("merged_from") or [{}])[0].get("key") or ("dlg%d" % order[gk])
        nparts = len(nd.get("merged_from") or [])
        name = "%05d_%s%s.json" % (order[gk], first_key,
                                   ("__full_%dparts" % nparts) if nparts > 1 else "")
        json.dump(nd, open(os.path.join(OUT, name), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    line = "%s | REBUILD: кусков %d -> полных диалогов %d" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), len(parts), len(groups))
    _p(line)
    try:
        with open(os.path.join(DATA, "_merge_log.txt"), "a", encoding="utf-8") as lf:
            lf.write(line + "\n")
    except Exception:
        pass


def main():
    os.makedirs(OUT, exist_ok=True)
    now = time.time()

    # 1) существующее хранилище: group_key -> (путь, dict)
    store = {}
    for fp in glob.glob(os.path.join(OUT, "*.json")):
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        gk = group_key(d)
        if gk:
            store[gk] = (fp, d)

    # 2) сырые куски из всех папок dialogs* (кроме merged)
    raw = []
    src_folders = [p for p in glob.glob(os.path.join(DATA, "dialogs*"))
                   if os.path.isdir(p) and os.path.basename(p) != "dialogs_merged"]
    skipped_fresh = 0
    for folder in src_folders:
        for fp in glob.glob(os.path.join(folder, "*.json")):
            try:
                if now - os.path.getmtime(fp) < SAFE_AGE_SEC:
                    skipped_fresh += 1
                    continue                      # возможно, пишется прямо сейчас
                d = json.load(open(fp, encoding="utf-8"))
            except Exception:
                continue
            raw.append((d.get("saved_at") or os.path.basename(fp), fp, d))
    raw.sort(key=lambda x: x[0])                  # старые куски первыми

    # 3) втягиваем куски в хранилище
    touched = {}                                  # gk -> dict (обновлённые группы)
    absorbed_files = []
    no_key = 0
    for _, fp, d in raw:
        gk = group_key(d)
        if not gk:                                # нет визитора — копия как есть, без группировки
            dst = os.path.join(OUT, os.path.basename(fp))
            if not os.path.exists(dst):
                json.dump(d, open(dst + ".tmp", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                os.replace(dst + ".tmp", dst)
            absorbed_files.append(fp)
            no_key += 1
            continue
        if gk in touched:
            cur = touched[gk]
        elif gk in store:
            cur = dict(store[gk][1])
        else:
            cur = None
        part_msgs = d.get("messages") or []
        if cur is None:
            nd = dict(d)
            nd.setdefault("merged_from", [])
        else:
            nd = cur
            nd["messages"] = merge_messages(nd.get("messages") or [], part_msgs)
            # свежая meta/служебные поля от нового куска
            for f in ("saved_at", "finish_reason", "meta"):
                if d.get(f):
                    nd[f] = d[f]
            ck_old = nd.get("conversation_key") or ""
            ck_new = d.get("conversation_key") or ""
            if ck_new and ck_new not in ck_old.split("+"):
                nd["conversation_key"] = (ck_old + "+" + ck_new) if ck_old else ck_new
        nd.setdefault("merged_from", [])
        nd["merged_from"].append({"file": os.path.basename(fp), "key": d.get("conversation_key"),
                                  "msgs": len(part_msgs)})
        nd["message_count"] = len(nd.get("messages") or [])
        touched[gk] = nd
        absorbed_files.append(fp)

    # 4) атомарная запись обновлённых групп
    written = 0
    for gk, nd in touched.items():
        if gk in store:
            path = store[gk][0]
        else:
            first = (nd.get("merged_from") or [{}])[0].get("file") or ("dlg_%d.json" % written)
            path = os.path.join(OUT, re.sub(r"\.json$", "", first) + "__full.json")
        tmp = path + ".tmp"
        json.dump(nd, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        written += 1

    # 5) исходники удаляем ТОЛЬКО после успешной записи всех групп
    # (приёмник пишет пару .json + .txt-копию для чтения глазами — сносим обе)
    deleted = 0
    for fp in absorbed_files:
        try:
            os.remove(fp)
            deleted += 1
        except Exception:
            pass
        txt = re.sub(r"\.json$", ".txt", fp)
        try:
            if os.path.exists(txt):
                os.remove(txt)
        except Exception:
            pass
    # опустевшие архивные папки убираем (живую dialogs оставляем приёмнику)
    for folder in src_folders:
        if os.path.basename(folder) != "dialogs":
            try:
                if not os.listdir(folder):
                    os.rmdir(folder)
            except Exception:
                pass

    total_store = len(glob.glob(os.path.join(OUT, "*.json")))
    line = ("%s | втянуто кусков %d (групп обновлено %d), удалено исходников %d, "
            "пропущено свежих %d | всего полных диалогов: %d" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), len(absorbed_files), written,
                deleted, skipped_fresh, total_store))
    _p(line)
    try:
        with open(os.path.join(DATA, "_merge_log.txt"), "a", encoding="utf-8") as lf:
            lf.write(line + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    if "--rebuild" in sys.argv:
        rebuild_from_jsonl()
    else:
        main()
