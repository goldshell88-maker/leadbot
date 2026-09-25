# -*- coding: utf-8 -*-
"""СТРЕСС-ТЕСТЫ: синтетические сценарии (easy/hard/weird) с машинными проверками.
Сценарии в stress_scenarios.json (сгенерированы по регламенту). Прогон через run_relay,
проверки regex по ответам бота — без судей, воспроизводимо и бесплатно на оценке.
"""
import sys, io, os, re, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import server  # noqa

HERE = os.path.dirname(os.path.abspath(__file__))
SC_PATH = os.path.join(HERE, "stress_scenarios.json")

ONLY = sys.argv[1] if len(sys.argv) > 1 else ""   # фильтр по difficulty/id


def run_scenario(sc):
    hist, rows = [], []
    for t in sc["turns"]:
        hist.append({"role": "client", "text": t})
        res = server.run_relay(hist, known={"direction": sc.get("direction") or None})
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


def main():
    scenarios = json.load(open(SC_PATH, encoding="utf-8"))
    if ONLY:
        scenarios = [s for s in scenarios if ONLY in (s.get("difficulty"), s.get("id"))]
    total_checks, passed_checks, failed = 0, 0, []
    out = []
    print("=== СТРЕСС: %d сценариев ===\n" % len(scenarios))
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
                ch["kind"], ch["turn"], ch.get("pattern", ""), ch["why"][:50], got))
        if dash:
            print("    !! ТИРЕ в ответе")
            sc_fail.append(({"kind": "dash"}, ""))
        if sc_fail:
            failed.append(sc["id"])
        print()
        out.append({"id": sc["id"], "difficulty": sc.get("difficulty"),
                    "rows": [{"client": r["client"], "bot": r["bot"]} for r in rows],
                    "fails": [c[0].get("kind") for c in sc_fail]})
    with open(os.path.join(HERE, "test_stress_results.jsonl"), "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    print("=" * 55)
    print("Проверок: %d/%d прошло | провальных сценариев: %d %s" % (
        passed_checks, total_checks, len(failed), (": " + ", ".join(failed)) if failed else ""))


if __name__ == "__main__":
    main()
