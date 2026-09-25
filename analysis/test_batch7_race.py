# -*- coding: utf-8 -*-
"""ПАЧКА 7, часть 2 — ОДНОВРЕМЕННЫЕ ХОДЫ НЕ ТЕРЯЮТ ПОЛЯ ЗАЯВКИ (аудит 22.08).

`run_turn` работала так: под замком прочитать состояние → ОТПУСТИТЬ замок →
позвать модель (секунды) → снова взять замок и ЗАПИСАТЬ своё состояние целиком.

Два хода по одному диалогу внахлёст перезаписывают друг друга: оба прочитали
состояние v0, первый дописал телефон, второй — адрес, и в словарь лёг только
второй снимок. Телефон, который клиент назвал, из заявки исчезает — молча.

Находка была записана 20.08 как Д-019 и оставалась открытой. Сегодня она
локализована точно: `run_turn` зовёт РОВНО ОДИН путь — ручка `/api/message`
(тест-чат панели). Живой канал Авито и мост LeadChat ходят стейтлесс через
`run_relay` и этой бухгалтерии не касаются. То есть сегодня цена — испорченная
проверка в панели, но путь остаётся заряженным на тот день, когда через него
пустят исходящий канал.

Заодно закрыт безграничный рост `_DIALOGS`: словарь только пополнялся, чистила
его лишь ручка `/api/reset`.

Запуск: uv run python analysis/test_batch7_race.py   (0 токенов, без сети)
"""
import io
import os
import sys
import threading
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import server  # noqa: E402

ok = bad = 0


def chk(label, cond):
    global ok, bad
    if cond:
        ok += 1
    else:
        bad += 1
        print("✗", label)


# ── подменяем модель: первый ход отдаёт телефон, второй адрес ───────────────
_настоящий = server.run_relay
_ходы = []


def _медленный_relay(msgs, known=None):
    n = len(_ходы)
    _ходы.append(n)
    time.sleep(0.25)                       # окно, в котором и жила гонка
    поле = {"phone": "+7 900 000-00-01"} if n == 0 else {"address": "Ленина 1"}
    return {"reply": "ответ %d" % n, "state": поле, "ready": False, "handoff": False,
            "model": "тест", "usage": None}


server.run_relay = _медленный_relay
try:
    server._DIALOGS.clear()
    ошибки = []

    def ход(текст):
        try:
            server.run_turn("гонка-1", текст)
        except Exception as e:            # noqa: BLE001 — падение тоже результат
            ошибки.append(repr(e))

    потоки = [threading.Thread(target=ход, args=("мой телефон",)),
              threading.Thread(target=ход, args=("мой адрес",))]
    for p in потоки:
        p.start()
    for p in потоки:
        p.join()

    chk("ни один ход не упал: %s" % (ошибки or "—"), not ошибки)
    состояние = (server._DIALOGS.get("гонка-1") or {}).get("state") or {}
    chk("телефон уцелел после одновременных ходов", bool(состояние.get("phone")))
    chk("адрес уцелел после одновременных ходов", bool(состояние.get("address")))
    chk("оба поля собраны в ОДНОМ состоянии: %s" % sorted(состояние),
        bool(состояние.get("phone")) and bool(состояние.get("address")))

    # ── замок на диалог, а не общий: разные диалоги не ждут друг друга ──────
    server._DIALOGS.clear()
    _ходы.clear()
    начало = time.time()
    пара = [threading.Thread(target=lambda: server.run_turn("чат-A", "раз")),
            threading.Thread(target=lambda: server.run_turn("чат-B", "два"))]
    for p in пара:
        p.start()
    for p in пара:
        p.join()
    прошло = time.time() - начало
    chk("разные диалоги идут параллельно: %.2f с (последовательно было бы ≥0.5)" % прошло,
        прошло < 0.45)
finally:
    server.run_relay = _настоящий

# ── _DIALOGS больше не растёт без предела ──────────────────────────────────
chk("потолок числа диалогов объявлен", hasattr(server, "_DIALOGS_MAX"))
server._DIALOGS.clear()
for i in range(server._DIALOGS_MAX + 30):
    server._new_dialog("d%d" % i)
chk("диалогов в памяти не больше потолка: %d" % len(server._DIALOGS),
    len(server._DIALOGS) <= server._DIALOGS_MAX)
chk("вытеснен самый старый диалог", "d0" not in server._DIALOGS)
chk("замки не копятся отдельно от диалогов",
    len(getattr(server, "_DIALOG_LOCKS", {})) <= server._DIALOGS_MAX + 5)

print("\nИТОГО: %d ок, %d провал" % (ok, bad))
sys.exit(1 if bad else 0)
