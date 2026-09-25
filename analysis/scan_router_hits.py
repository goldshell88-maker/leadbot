# -*- coding: utf-8 -*-
"""СКАН КОРПУСА НА ЛОЖНЫЕ СРАБАТЫВАНИЯ РОУТЕРОВ (0 токенов, шлюз не нужен).

Каждая новая ветка _REFUSE/_SOGLAS может молча похоронить живой лид (было: улица Халтурина
в жалобах, base64 в мусоре, «бАНАЛЬНо» в интиме). Скрипт прогоняет роутеры по ПОЛНОМУ корпусу
dialogs_merged и показывает, что именно они ловят, — глазами видно ложные.

Запуск:
  py analysis\\scan_router_hits.py                    — сводка по всем роутерам
  py analysis\\scan_router_hits.py soglas             — только согласования, с примерами
  py analysis\\scan_router_hits.py soglas "сушилк"    — только ветки, чьё имя содержит подстроку
  py analysis\\scan_router_hits.py rx "наружн\\w*\\s*сушилк"  — произвольный регекс (проверка кандидата)

Смотрит ПЕРВЫЕ сообщения клиента (там решается судьба лида) и, для полноты, все остальные.
"""
import sys, io, os, re, json, glob, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))
import prefilter as p                                    # noqa: E402
import paths                                             # noqa: E402

MAX_SAMPLES = 12


def _client_texts(d):
    """(первое сообщение клиента, [все сообщения клиента])."""
    msgs = d.get("messages") or []
    cl = [(m.get("text") or "").strip() for m in msgs if m.get("role") in ("client", "visitor", "user")]
    cl = [t for t in cl if t]
    return (cl[0] if cl else ""), cl


def load_corpus():
    files = sorted(glob.glob(os.path.join(paths.MERGED, "*.json")))
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                yield os.path.basename(fp), json.load(f)
        except Exception:
            continue


def scan(kind, name_filter="", custom_rx=None):
    hits = collections.defaultdict(list)          # ветка → [(файл, первое?, текст)]
    n_dialogs = 0
    for fname, d in load_corpus():
        n_dialogs += 1
        first, all_cl = _client_texts(d)
        for t in all_cl:
            low = t.lower()
            is_first = (t == first)
            if custom_rx is not None:
                if custom_rx.search(low):
                    hits["регекс"].append((fname, is_first, t))
                continue
            if kind in ("all", "refuse"):
                r = p.check_refuse(t)
                if r:
                    hits["ОТКАЗ/" + r.get("sub", "?")].append((fname, is_first, t))
            if kind in ("all", "soglas"):
                r = p.check_soglasovanie(t)
                if r:
                    hits["СОГЛАС/" + r.get("sub", "?")].append((fname, is_first, t))
            if kind in ("all", "complaint"):
                if p.check_complaint(t):
                    hits["ЖАЛОБА"].append((fname, is_first, t))
            if kind in ("all", "junk"):
                if p.check_junk(t, prior_client_content=not is_first):
                    hits["МУСОР"].append((fname, is_first, t))
    print("Корпус: %d диалогов (%s)" % (n_dialogs, paths.MERGED))
    total = first_total = 0
    for branch in sorted(hits, key=lambda k: -len(hits[k])):
        if name_filter and name_filter.lower() not in branch.lower():
            continue
        rows = hits[branch]
        firsts = sum(1 for _f, isf, _t in rows if isf)
        total += len(rows)
        first_total += firsts
        print("\n%-58s сработок %4d (из них на ПЕРВОМ сообщении %d)" % (branch, len(rows), firsts))
        if kind != "all" or custom_rx is not None:
            for fname, isf, t in rows[:MAX_SAMPLES]:
                print("   %s %s | %s" % ("★1-е" if isf else "    ", fname[:26], t.replace("\n", " ")[:150]))
            if len(rows) > MAX_SAMPLES:
                print("   ... ещё %d" % (len(rows) - MAX_SAMPLES))
    print("\nВСЕГО сработок: %d, из них на первом сообщении: %d" % (total, first_total))
    return 0


def main():
    args = sys.argv[1:]
    if args and args[0] == "rx":
        if len(args) < 2:
            print("нужен регекс: py analysis\\scan_router_hits.py rx \"наружн\\w*\\s*сушилк\"")
            return 2
        return scan("none", custom_rx=re.compile(args[1]))
    kind = args[0] if args else "all"
    if kind not in ("all", "refuse", "soglas", "complaint", "junk"):
        print("вид: all | refuse | soglas | complaint | junk | rx <регекс>")
        return 2
    return scan(kind, args[1] if len(args) > 1 else "")


if __name__ == "__main__":
    sys.exit(main())
