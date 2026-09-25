# -*- coding: utf-8 -*-
"""НЕПРЕДВИДЕННОЕ: корпус «того, что владелец не предвидел» (запрос 16.08).
Сценарии в unforeseen_scenarios.json (5 категорий: края тематики, эмоции,
форматы-шум, процессные ловушки, давление на правила). Прогон через run_relay,
regex-проверки + полные диалоги в unforeseen_results.jsonl на судейскую оценку.
"""
import sys, io, os, re, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import server  # noqa

HERE = os.path.dirname(os.path.abspath(__file__))
SC_PATH = os.path.join(HERE, "unforeseen_scenarios.json")

ONLY = sys.argv[1] if len(sys.argv) > 1 else ""   # фильтр по difficulty/id


def _relay_retry(hist, known, tries=5):
    # шлюз вечером рвёт соединения (RemoteDisconnected) — прогон не должен
    # умирать на 40-м сценарии из-за одного сетевого чиха
    import time as _t
    for i in range(tries):
        try:
            return server.run_relay(hist, known=known)
        except Exception as e:  # noqa: BLE001 — сетевые и только сетевые
            if i == tries - 1:
                raise
            print("    …сеть (%s), повтор %d/%d" % (type(e).__name__, i + 2, tries))
            _t.sleep(8 * (i + 1))


def run_scenario(sc):
    hist, rows = [], []
    for t in sc["turns"]:
        hist.append({"role": "client", "text": t})
        res = _relay_retry(hist, {"direction": sc.get("direction") or None})
        reply = (res.get("reply") or "").strip()
        rows.append({"client": t, "bot": reply, "res": res})
        hist.append({"role": "operator", "text": reply})
    return rows


def check_one(rows, ch):
    ti = ch["turn"]
    if ti >= len(rows):
        return False, "нет хода %d" % ti
    row = rows[ti]
    b = row["bot"].lower()
    kind = ch["kind"]
    if kind == "must_match":
        return bool(re.search(ch.get("pattern") or "", b)), row["bot"][:70]
    if kind == "must_not_match":
        return not re.search(ch.get("pattern") or "", b), row["bot"][:70]
    if kind == "handoff_true":
        return bool(row["res"].get("handoff") or row["res"].get("to_operator")
                    or row["res"].get("call_needed")), row["bot"][:70]
    if kind == "nonempty":
        return bool(b.strip()), row["bot"][:70]
    if kind == "empty_or_handoff":
        return (not b.strip()) or bool(row["res"].get("handoff") or row["res"].get("to_operator")), row["bot"][:70]
    return True, "?"


RES_PATH = os.path.join(HERE, "unforeseen_results.jsonl")


def main():
    scenarios = json.load(open(SC_PATH, encoding="utf-8"))
    if ONLY:
        scenarios = [s for s in scenarios if ONLY in (s.get("difficulty"), s.get("id"))]
    # резюм: сеть на Mac моргает (VPN), упавший прогон продолжается с места
    done = set()
    if os.path.exists(RES_PATH) and not ONLY:
        for ln in open(RES_PATH, encoding="utf-8"):
            try:
                done.add(json.loads(ln)["id"])
            except Exception:
                pass
        if done:
            print("резюм: %d сценариев уже пройдено, пропускаю" % len(done))
        scenarios = [s for s in scenarios if s["id"] not in done]
    total_checks, passed_checks, failed = 0, 0, []
    out = []
    print("=== НЕПРЕДВИДЕННОЕ: %d сценариев ===\n" % len(scenarios))
    for sc in scenarios:
        rows = run_scenario(sc)
        sc_fail = []
        for ch in sc.get("checks", []):
            total_checks += 1
            ok, got = check_one(rows, ch)
            if ok:
                passed_checks += 1
            else:
                sc_fail.append((ch, got))
        dash = any("—" in r["bot"] for r in rows)
        mark = "✗" if sc_fail or dash else "✓"
        print("%s [%s/%s] %s" % (mark, sc.get("difficulty"), sc.get("direction"), sc["id"]))
        for r in rows:
            print("    К: %s" % r["client"][:64])
            print("    Б: %s" % (r["bot"][:72] if r["bot"] else "(пусто/оператору)"))
        for ch, got in sc_fail:
            print("    !! FAIL %s turn=%d %s (%s): получили «%s»" % (
                ch["kind"], ch["turn"], ch.get("pattern", ""), (ch.get("why") or ch.get("note") or "")[:50], got))
        if dash:
            print("    !! ТИРЕ в ответе")
            sc_fail.append(({"kind": "dash"}, ""))
        if sc_fail:
            failed.append(sc["id"])
        print()
        rec = {"id": sc["id"], "difficulty": sc.get("difficulty"),
               "rows": [{"client": r["client"], "bot": r["bot"]} for r in rows],
               "fails": [c[0].get("kind") for c in sc_fail]}
        out.append(rec)
        # дозапись сразу: падение сети не сжигает пройденное
        with open(RES_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("=" * 55)
    print("Проверок: %d/%d прошло | провальных сценариев: %d %s" % (
        passed_checks, total_checks, len(failed), (": " + ", ".join(failed)) if failed else ""))
    # ⚠ НАБОР, КОТОРЫЙ НИЧЕГО НЕ ПРОВЕРИЛ, ОБЯЗАН КРАСНЕТЬ (F-001, этап 0 программы
    # обучения). С мёртвым шлюзом раннер печатал «Проверок: 0/0» и выходил кодом 0 —
    # то есть 42 сценария непредвиденных диалогов молча не проверялись, а в любом
    # автоматическом прогоне набор был вечнозелёным. Несгораемость (дозапись и резюм)
    # нужна против ОБРЫВОВ, а не против полного отказа модели: отличать их обязан код,
    # а не человек, читающий вывод.
    if total_checks == 0:
        print("ПРОВАЛ: ни одной проверки не выполнено — шлюз недоступен или корпус пуст")
        sys.exit(2)
    доля_пустых = sum(1 for r in out if not any(x.get("bot") for x in r.get("rows", []))) / max(1, len(out))
    if доля_пустых > 0.5:
        print("ПРОВАЛ: %.0f%% сценариев без единого ответа бота — прогон недействителен"
              % (100 * доля_пустых))
        sys.exit(2)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
