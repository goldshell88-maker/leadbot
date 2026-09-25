# -*- coding: utf-8 -*-
"""Тест ЖИВОСТИ общения и РАБОТЫ С КОНТЕКСТОМ в многоходовом диалоге.

Прогоняет бота ход-за-ходом по сценариям, которые СПЕЦИАЛЬНО нагружают контекст:
повторные вопросы, сброс на приветствие, забывание деталей, флип-флоп времени,
разрешение ссылок («это»), исправления клиента, многосоставные сообщения, сленг.
Ловит детерминированные ляпы (повторное приветствие, роботизмы) и сохраняет диалоги
для судейской оценки живости/контекста. Прогон через шлюз (in-process run_relay).
"""
import sys, io, os, re, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import server  # noqa

HERE = os.path.dirname(os.path.abspath(__file__))

# (id, known, [реплики клиента по ходам], на что смотрим)
SCENARIOS = [
    ("piecemeal_no_reask", {"direction": "bt"}, [
        "Здравствуйте, стиральная машина Bosch не отжимает",
        "барабан крутится, но вода остаётся",
        "а когда сможете приехать?",
        "давайте сегодня",
    ], "не переспрашивать модель/проблему; держать к записи"),
    ("greeting_reset", {"direction": "mnc"}, [
        "Добрый день, нужно повесить люстру",
        "да, потолок бетонный, крюк есть",
        "ок",
        "сегодня вечером можно?",
    ], "здороваться ОДИН раз, не сбрасываться на «здравствуйте, что нужно?»"),
    ("name_city_known", {"visitor": "Олег", "city": "Пермь", "direction": "kp"}, [
        "не включается ноутбук",
        "asus, лежал на зарядке, потом погас",
        "сколько будет стоить?",
    ], "не переспрашивать имя/город; ответить по цене КП"),
    ("reference_resolution", {"direction": "bt"}, [
        "здравствуйте, холодильник Атлант не морозит",
        "а сколько это будет стоить?",
        "понятно, а приехать когда можете?",
    ], "«это» = ремонт холодильника, не спрашивать «что это?»"),
    ("client_correction", {"direction": "bt"}, [
        "телевизор не включается",
        "самсунг, 50 дюймов",
        "хотя нет, извините, перепутала — это LG",
        "и когда приедете?",
    ], "принять исправление модели, не путаться"),
    ("time_no_flipflop", {"direction": "mnc"}, [
        "нужно смеситель на кухне поменять",
        "а во сколько сегодня могли бы?",
        "хм, а пораньше никак?",
        "ну ладно, надо подумать",
    ], "держать предложенный слот, без флип-флопа время"),
    ("multipart_message", {"direction": "mnc"}, [
        "Здравствуйте! Нужно собрать шкаф-купе, инструкция есть, район Центральный, сегодня после 18 сможете?",
        "да, всё верно",
    ], "не переспрашивать уже данное (схема/район/время), двигать к телефону/адресу"),
    ("slang_casual", {"direction": "bt"}, [
        "прив, у меня тут холодос потёк снизу, чё делать",
        "ну лужа под ним каждое утро",
        "а по деньгам как?",
    ], "ответить живо и по-человечески на сленг, принять заявку"),
    ("persistent_price_kp", {"direction": "kp"}, [
        "здравствуйте, компьютер не включается",
        "сколько за ремонт?",
        "а точнее хоть примерно?",
        "а выезд-то платный?",
    ], "КП: выезд+диагностика 1000; не терять контекст, без вилки, без роботизмов"),
    ("bare_ping_after_address", {"direction": "bt"}, [
        "посудомойка не сушит",
        "Bosch",
        "адрес Ленина 10, кв 5",
        "?",
    ], "после адреса «?» = дожать к телефону/времени, НЕ сбрасываться"),
]

GREET = re.compile(r"здравствуй|добрый день|добрый вечер|доброго дня|доброго вечера|приветству|\bприв\b", re.I)
ROBO = re.compile(r"по уточнени|уточнение:|по регламент|фиксиру|обрабатыва\w* запрос|"
                  r"по вашему обращени|для уточнения детал|обработка", re.I)


def run_scenario(sc_id, known, turns):
    hist = []
    rows = []
    for t in turns:
        hist.append({"role": "client", "text": t})
        res = server.run_relay(hist, known=known)
        reply = (res.get("reply") or "").strip()
        rows.append({"client": t, "bot": reply, "model": res.get("model"),
                     "to_operator": bool(res.get("to_operator"))})
        # добавляем ответ бота в историю как оператора (для контекста следующего хода)
        hist.append({"role": "operator", "text": reply})
    return rows


def main():
    out = []
    print("=== ЖИВОСТЬ + КОНТЕКСТ: %d сценариев ===\n" % len(SCENARIOS))
    tot_greet_reset, tot_robo = 0, 0
    for sc_id, known, turns, focus in SCENARIOS:
        rows = run_scenario(sc_id, known, turns)
        bots = [r["bot"] for r in rows if r["bot"]]
        greet_hits = sum(1 for b in bots if GREET.search(b))
        robo_hits = [b for b in bots if ROBO.search(b)]
        greet_reset = greet_hits > 1                    # поздоровался больше одного раза = сброс
        if greet_reset:
            tot_greet_reset += 1
        if robo_hits:
            tot_robo += 1
        flags = []
        if greet_reset:
            flags.append("ПОВТОРНОЕ ПРИВЕТСТВИЕ (%d)" % greet_hits)
        if robo_hits:
            flags.append("РОБОТИЗМ: " + " | ".join(robo_hits)[:80])
        print("• %-24s %s" % (sc_id, ("⚠ " + "; ".join(flags)) if flags else "✓ чисто (детерм.)"))
        print("   фокус: %s" % focus)
        for r in rows:
            print("   К: %s" % r["client"][:70])
            print("   Б: %s" % (r["bot"][:78] if r["bot"] else "(пусто → оператору)"))
        print()
        out.append({"id": sc_id, "known": known, "focus": focus, "rows": rows,
                    "greet_reset": greet_reset, "robo": bool(robo_hits)})
    with open(os.path.join(HERE, "test_context_liveness.jsonl"), "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    print("=" * 55)
    print("Детерминированно: повторное приветствие в %d/%d, роботизмы в %d/%d сценариях"
          % (tot_greet_reset, len(SCENARIOS), tot_robo, len(SCENARIOS)))
    print("Диалоги → test_context_liveness.jsonl (на судейскую оценку живости/контекста)")


if __name__ == "__main__":
    main()
