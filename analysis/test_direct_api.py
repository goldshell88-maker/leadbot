# -*- coding: utf-8 -*-
"""ДЫМОВОЙ ТЕСТ ПРЯМОГО API ANTHROPIC (вместо шлюза).

Проверяет по порядку:
  1) откуда берётся ключ и какой стиль авторизации уйдёт (ключ НЕ печатается);
  2) минимальный вызов /v1/messages — работает ли авторизация;
  3) полный прод-путь бота (run_relay) на синтетическом диалоге;
  4) реальную стоимость хода по usage из ответа.

Ключ берётся из переменной окружения LEADBOT_API_KEY (или ANTHROPIC_API_KEY).
Запуск:  py analysis\\test_direct_api.py
"""
import sys, io, os, json, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))

# ключ мог быть положен в ANTHROPIC_API_KEY — поддержим оба имени
if not os.environ.get("LEADBOT_API_KEY") and os.environ.get("ANTHROPIC_API_KEY"):
    os.environ["LEADBOT_API_KEY"] = os.environ["ANTHROPIC_API_KEY"]
os.environ.setdefault("LEADBOT_API_BASE", "https://api.anthropic.com")

import claude_api
import server

# цены Anthropic, $ за миллион токенов: (вход, выход, чтение кэша, запись кэша 1ч)
PRICES = {
    "claude-sonnet-5":  (2.00, 10.00, 0.20, 4.00),      # вводная цена до 31.08.2026
    "claude-haiku-4-5": (1.00,  5.00, 0.10, 2.00),
}


def price_for(model):
    for key, p in PRICES.items():
        if (model or "").startswith(key):
            return p
    return None


def cost_usd(model, u):
    p = price_for(model)
    if not p or not u:
        return None
    inp, out, cread, cwrite = p
    return (u.get("input_tokens", 0) * inp
            + u.get("output_tokens", 0) * out
            + u.get("cache_read_input_tokens", 0) * cread
            + u.get("cache_creation_input_tokens", 0) * cwrite) / 1_000_000.0


def main():
    cfg = claude_api.config()
    key = cfg.get("auth_token") or ""
    base = cfg.get("gateway_base")

    print("=" * 60)
    print("1. КОНФИГУРАЦИЯ")
    print("   база:          %s" % base)
    print("   ключ:          %s (длина %d)" % (
        ("задан" if key else "НЕ ЗАДАН — выполните setx LEADBOT_API_KEY ..."), len(key)))
    print("   авторизация:   %s" % list(claude_api._auth_headers(cfg).keys())[0])
    print("   модели:        %s / %s" % (cfg.get("model_hard"), cfg.get("model_simple")))
    print("   thinking:      %s, effort: %s" % (cfg.get("thinking"), cfg.get("effort")))
    if not key:
        return 1

    print("\n2. МИНИМАЛЬНЫЙ ВЫЗОВ")
    try:
        t0 = time.time()
        r = claude_api.messages(
            system="Отвечай одним словом.",
            msgs=[{"role": "user", "content": "Скажи: работает"}],
            model=cfg.get("model_simple"), max_tokens=16)
        txt = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
        print("   OK  %.1fс | модель %s | ответ: %s" % (time.time() - t0, r.get("model"), txt.strip()))
        print("   usage: %s" % json.dumps(r.get("usage", {}), ensure_ascii=False))
    except Exception as e:
        print("   ПРОВАЛ: %s" % e)
        return 1

    print("\n3. ПОЛНЫЙ ПУТЬ БОТА (run_relay)")
    dialog = [{"role": "client", "text": "Здравствуйте, не крутит барабан у стиральной машины"}]
    try:
        t0 = time.time()
        res = server.run_relay(dialog, known={"city": "", "persona": "p1"})
        print("   OK  %.1fс | модель: %s" % (time.time() - t0, res.get("model")))
        print("   ответ бота: %s" % (res.get("reply") or "(пусто)").replace("\n", " / "))
        u = (res.get("usage") or {})
        if u:
            print("   usage: %s" % json.dumps(u, ensure_ascii=False))
            c = cost_usd(res.get("model"), u)
            if c is not None:
                print("   стоимость хода: $%.5f" % c)
    except Exception as e:
        print("   ПРОВАЛ: %s" % e)
        return 1

    print("\n4. ВТОРОЙ ХОД (проверка чтения кэша плейбука)")
    dialog += [{"role": "bot", "text": res.get("reply") or "гляну"},
               {"role": "client", "text": "а сколько будет стоить?"}]
    try:
        res2 = server.run_relay(dialog, known={"city": "", "persona": "p1"})
        u2 = (res2.get("usage") or {})
        print("   ответ бота: %s" % (res2.get("reply") or "(пусто)").replace("\n", " / "))
        if u2:
            print("   usage: %s" % json.dumps(u2, ensure_ascii=False))
            cr = u2.get("cache_read_input_tokens", 0)
            print("   чтение кэша: %d токенов — %s" % (
                cr, "кэш РАБОТАЕТ" if cr > 0 else "кэш НЕ читается, проверить cache_control"))
            c2 = cost_usd(res2.get("model"), u2)
            if c2 is not None:
                print("   стоимость хода: $%.5f" % c2)
    except Exception as e:
        print("   ПРОВАЛ: %s" % e)
        return 1

    print("\n" + "=" * 60)
    print("ГОТОВО. Прямой API работает.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
