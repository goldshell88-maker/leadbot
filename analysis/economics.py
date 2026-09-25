# -*- coding: utf-8 -*-
"""ЭКОНОМИКА БОТА: сколько стоит день работы и сколько приносит (0 токенов).

Считает по РЕАЛЬНЫМ замерам, а не по прикидкам:
  • токены на ход — средние из analysis/cost_log.jsonl (последние записи, прямой API);
  • ходов на диалог — из корпуса dialogs_merged;
  • доля диалогов, которые перехватывают роутеры (0 токенов) — прогоном роутеров по корпусу;
  • доля попаданий в кэш ответов — из размера кэша против числа вызовов модели.

Тарифы Anthropic (справочник claude-api, на 01.08.2026), $ за 1 млн токенов:
  Sonnet 5   вход 3.00 / выход 15.00  (вводная цена 2.00 / 10.00 до 31.08.2026)
  Haiku 4.5  вход 1.00 / выход 5.00
Кэш: чтение ~0.1× от цены входа; запись 1.25× (TTL 5 мин) или 2× (TTL 1 час).
У нас в конфиге cache_ttl = 1h, поэтому запись считаем по 2×.

Запуск:  py analysis\\economics.py [диалогов_в_сутки] [курс_рубля]
"""
import sys, io, os, json, glob, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))
import paths                                             # noqa: E402
import prefilter as p                                    # noqa: E402

# ── тарифы, $/1М токенов ────────────────────────────────────────────────────────
PRICE = {
    "claude-sonnet-5":            {"in": 3.00, "out": 15.00, "intro_in": 2.00, "intro_out": 10.00},
    "claude-haiku-4-5-20251001":  {"in": 1.00, "out": 5.00},
}
CACHE_READ_MULT = 0.1     # чтение кэша — десятая часть цены входа
CACHE_WRITE_MULT = 2.0    # запись с TTL 1 час (при TTL 5 минут было бы 1.25)


def load_cost_log(since="2026-07-30"):
    """Средние токены на ОДИН вызов модели, по моделям (свежие записи)."""
    agg = collections.defaultdict(lambda: collections.Counter())
    for ln in open(os.path.join(ROOT, "analysis", "cost_log.jsonl"), encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if (r.get("t") or "") < since:
            continue
        m = r.get("model", "?")
        agg[m]["n"] += 1
        for k in ("in", "cache_read", "cache_write", "out"):
            agg[m][k] += int(r.get(k) or 0)
    return agg


def corpus_stats():
    """Ходов на диалог и доля диалогов, закрытых роутером без модели."""
    n = bot = routed = withphone = 0
    for fp in glob.glob(os.path.join(paths.MERGED, "*.json")):
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        msgs = d.get("messages") or []
        cl = [(m.get("text") or "") for m in msgs if m.get("role") in ("client", "visitor", "user")]
        op = [(m.get("text") or "") for m in msgs if m.get("role") in ("operator", "agent", "bot")]
        cl = [x for x in cl if x.strip()]
        if not cl:
            continue
        n += 1
        bot += len([x for x in op if x.strip()])
        if (p.check_refuse(cl[0]) or p.check_soglasovanie(cl[0])
                or p.check_junk(cl[0], prior_client_content=False) or p.check_complaint(cl[0])):
            routed += 1
        if p.phones_in(" ".join(cl)):
            withphone += 1
    return {"dialogs": n, "bot_turns": bot / n, "routed_share": routed / n,
            "phone_share": withphone / n}


def cost_per_call(model, tok, intro=True):
    """Стоимость одного вызова модели в долларах."""
    pr = PRICE.get(model) or PRICE["claude-sonnet-5"]
    pin = pr.get("intro_in", pr["in"]) if intro else pr["in"]
    pout = pr.get("intro_out", pr["out"]) if intro else pr["out"]
    return (tok["in"] * pin
            + tok["cache_read"] * pin * CACHE_READ_MULT
            + tok["cache_write"] * pin * CACHE_WRITE_MULT
            + tok["out"] * pout) / 1_000_000


def main():
    dialogs_day = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    rub = float(sys.argv[2]) if len(sys.argv) > 2 else 80.0
    PAY = 60.0                      # рублей за принятую заявку

    log = load_cost_log()
    cs = corpus_stats()

    print("=" * 74)
    print("ЗАМЕРЫ (не прикидки)")
    print("=" * 74)
    print("Корпус: %d диалогов | ходов бота на диалог %.2f" % (cs["dialogs"], cs["bot_turns"]))
    print("Роутеры закрывают без модели: %.1f%% диалогов (0 токенов)" % (100 * cs["routed_share"]))
    print("Телефон клиента появляется в %.1f%% диалогов (потолок конверсии в лид)"
          % (100 * cs["phone_share"]))
    print()
    total_calls = sum(c["n"] for c in log.values())
    per_call = {}
    for m, c in log.items():
        n = c["n"]
        tok = {k: c[k] / n for k in ("in", "cache_read", "cache_write", "out")}
        per_call[m] = (n / total_calls, tok, cost_per_call(m, tok))
        print("%-30s доля %4.1f%% | in %5.0f  cache_r %6.0f  cache_w %5.0f  out %4.0f | $%.5f/ход"
              % (m, 100 * n / total_calls, tok["in"], tok["cache_read"], tok["cache_write"],
                 tok["out"], per_call[m][2]))

    call_cost = sum(share * cost for share, _t, cost in per_call.values())
    print("\nСредний ход через модель: $%.5f  (%.3f ₽ при курсе %.0f)" % (call_cost, call_cost * rub, rub))
    # с 01.09 вводная цена Sonnet (2/10) заканчивается — тот же ход по полному тарифу (3/15)
    call_cost_sep = 0.0
    for m, (share, tok, _c) in per_call.items():
        pr = PRICE.get(m) or {}
        cin, cout = pr.get("in", 0), pr.get("out", 0)
        c = (tok["in"] * cin + tok["cache_read"] * cin * CACHE_READ_MULT
             + tok["cache_write"] * cin * CACHE_WRITE_MULT + tok["out"] * cout) / 1e6
        call_cost_sep += share * c
    if call_cost > 0:
        print("С 01.09 (конец вводной цены Sonnet): $%.5f/ход — ×%.2f к августовской смете"
              % (call_cost_sep, call_cost_sep / call_cost))

    # ходов через модель на диалог: минус роутерные диалоги, минус попадания кэша
    print()
    print("=" * 74)
    print("РАСХОД на %d диалогов в сутки" % dialogs_day)
    print("=" * 74)
    # ⚠ 16.08: замер по бою — кэш ответов попадает в 1.1% вызовов, а не в 30%.
    # Строки 30/50% оставлены как «если кэш когда-нибудь оживёт», решение о смете
    # принимать по строке 1.1%.
    for cache_hit in (0.011, 0.30, 0.50):
        model_dialogs = dialogs_day * (1 - cs["routed_share"])
        calls = model_dialogs * cs["bot_turns"] * (1 - cache_hit)
        day = calls * call_cost * rub
        метка = "ЗАМЕР 15.08" if cache_hit < 0.02 else "гипотеза"
        print("  кэш ответов %4.1f%% (%s) → вызовов модели %6.0f/сут | %8.0f ₽/сут | %9.0f ₽/мес"
              % (100 * cache_hit, метка, calls, day, day * 30))

    print()
    print("=" * 74)
    print("ДОХОД: %.0f ₽ за заявку, которая дожила до выезда мастера" % PAY)
    print("=" * 74)
    calls = dialogs_day * (1 - cs["routed_share"]) * cs["bot_turns"] * (1 - 0.011)
    day_cost = calls * call_cost * rub
    print("  (расход взят при РЕАЛЬНОМ кэше 1.1%%: %.0f ₽/сут)" % day_cost)
    print()
    print("  %-9s %-9s %-10s %12s %12s %12s" %
          ("лид→заявка", "доезд", "заявок/сут", "доход/сут", "прибыль/сут", "прибыль/мес"))
    for lead in (0.10, 0.15, 0.21):
        for alive in (0.50, 0.70):
            z = dialogs_day * lead * alive
            rev = z * PAY
            print("  %-9.0f%% %-9.0f%% %-10.0f %10.0f ₽ %10.0f ₽ %10.0f ₽"
                  % (100 * lead, 100 * alive, z, rev, rev - day_cost, (rev - day_cost) * 30))
    print()
    print("Порог безубыточности: %.1f заявки в сутки (%.2f%% от %d диалогов)"
          % (day_cost / PAY, 100 * (day_cost / PAY) / dialogs_day, dialogs_day))
    return 0


if __name__ == "__main__":
    sys.exit(main())
