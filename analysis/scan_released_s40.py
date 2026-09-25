# -*- coding: utf-8 -*-
"""ЧТО ОСВОБОДИЛИ И ЧТО ЗАХВАТИЛИ ГАРДЫ СЕССИИ 40 (0 токенов).

Обязательный шаг ритуала после правки роутера: новая ветка хоронит лиды, новый гард
отпускает согласуемое — обе ошибки молчаливые. Скрипт держит в одном процессе СТАРОЕ
поведение (монки-патч регексов на версии до правок) и НОВОЕ, и печатает разницу пофайлово.
"""
import sys, io, os, re, json, glob, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))
import prefilter as p                                    # noqa: E402
import paths                                             # noqa: E402

# ---- снимок СТАРЫХ регексов (до правок сессии 40) ----
OLD = {
    "скупка": re.compile(r"на\s*запчаст|скупк|выкуп\w*\s*(?:техник|телевизор|ноутбук|компьютер|у\s*вас)|"
                         r"выкупаете|скупаете|прода[мю]\w*\s*(?:нерабоч|неисправ|сломанн|битый|разбит)|"
                         r"прода\w*\s*на\s*запчаст|берёте\w*\s*на\s*запчаст|берете\w*\s*на\s*запчаст"),
    "светодиодный светильник": re.compile(r"светодиодн\w*\s*(?:люстр|светильник|бра|панел)"),
    "перетяжка/пружины мебели": re.compile(r"перетяж|перетян\w*\s*мебел|пружинн\w*\s*блок"),
}
OLD_GAS_DIRECT = re.compile(
    r"(?:подключ|отключ|отсоедин|перенест|перевес|установ|почин|ремонт|отремонт|замен)\w*"
    r"[^.!?]{0,30}(?:газов|\bгаз\b)|(?:газов\w*|\bгаз\b)[^.!?]{0,30}"
    r"(?:подключ|отключ|не\s*работает|не\s*греет|не\s*горит|почин|ремонт|теч|пахнет|утечк)")

NEW_REFUSE = list(p._REFUSE)
NEW_SOGLAS = list(p._SOGLAS)
NEW_GAS_DIRECT = p._GAS_DIRECT
NEW_MIXED = p._refuse_is_mixed


def _swap_old():
    p._REFUSE[:] = [(k, OLD.get(k, rx), r) for k, rx, r in NEW_REFUSE]
    p._SOGLAS[:] = [(k, OLD.get(k, rx)) for k, rx in NEW_SOGLAS]
    p._GAS_DIRECT = OLD_GAS_DIRECT
    p._refuse_is_mixed = lambda kind, t, rx: False


def _swap_new():
    p._REFUSE[:] = NEW_REFUSE
    p._SOGLAS[:] = NEW_SOGLAS
    p._GAS_DIRECT = NEW_GAS_DIRECT
    p._refuse_is_mixed = NEW_MIXED


def verdict(t):
    r = p.check_refuse(t)
    if r:
        return "ОТКАЗ/" + r.get("sub", "?")
    r = p.check_soglasovanie(t)
    if r:
        return "СОГЛАС/" + r.get("sub", "?")
    return ""


def main():
    rows = []
    for fp in sorted(glob.glob(os.path.join(paths.MERGED, "*.json"))):
        try:
            with open(fp, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        msgs = d.get("messages") or []
        cl = [(m.get("text") or "").strip() for m in msgs if m.get("role") in ("client", "visitor", "user")]
        cl = [t for t in cl if t]
        if not cl:
            continue
        rows.append((os.path.basename(fp), cl[0], cl))

    _swap_old()
    old = {}
    for fname, first, cl in rows:
        for t in cl:
            old[(fname, t)] = verdict(t)
    _swap_new()
    new = {}
    for fname, first, cl in rows:
        for t in cl:
            new[(fname, t)] = verdict(t)

    firsts = {(f, first) for f, first, _ in rows}
    released = collections.defaultdict(list)
    captured = collections.defaultdict(list)
    for key in old:
        o, n = old[key], new[key]
        if o == n:
            continue
        mark = "★1-е " if key in firsts else "     "
        if o and not n:
            released[o].append((mark, key[0], key[1]))
        elif n and not o:
            captured[n].append((mark, key[0], key[1]))
        else:
            released[o + " -> " + n].append((mark, key[0], key[1]))

    print("=" * 78)
    print("ОСВОБОЖДЕНО ГАРДАМИ (шаблон больше НЕ уходит — проверить, что это правда живые лиды)")
    print("=" * 78)
    for br in sorted(released, key=lambda k: -len(released[k])):
        print("\n%-52s %d" % (br, len(released[br])))
        for mark, fname, t in released[br][:25]:
            print("   %s%s | %s" % (mark, fname[:26], t.replace("\n", " ")[:140]))
        if len(released[br]) > 25:
            print("   ... ещё %d" % (len(released[br]) - 25))

    print("\n" + "=" * 78)
    print("ЗАХВАЧЕНО НОВЫМИ ВЕТКАМИ (шаблон уходит теперь — проверить, что это правда не наши)")
    print("=" * 78)
    for br in sorted(captured, key=lambda k: -len(captured[k])):
        print("\n%-52s %d" % (br, len(captured[br])))
        for mark, fname, t in captured[br][:25]:
            print("   %s%s | %s" % (mark, fname[:26], t.replace("\n", " ")[:140]))
        if len(captured[br]) > 25:
            print("   ... ещё %d" % (len(captured[br]) - 25))
    return 0


if __name__ == "__main__":
    sys.exit(main())
