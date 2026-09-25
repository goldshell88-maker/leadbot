# -*- coding: utf-8 -*-
"""ПИНГ МОЛЧАЩЕМУ И «РЕШЁН» (регламент, решение заказчика 15.08).

«Клиент не отвечает после нашего ответа: 5 минут → уточняющий вопрос,
ещё 10 минут → диалог решён». Офлайн: avito_api застаблен, модель не зовётся.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import channel  # noqa: E402

import calendar  # noqa: E402

SENT = []
channel.avito_api.send = lambda acc, chat_id, text: SENT.append((chat_id, text))

ACC = {"id": "a1", "auto_reply": True}
CHAT = {"id": "chat-1"}
# ⚠ моменты в UTC: окно пинга считается по ГОРОДУ клиента (пустой город = Москва,
# UTC+3), а машина, где идут тесты, может жить в любом поясе (Mac владельца — +10)
DAY = calendar.timegm((2026, 8, 17, 11, 0, 0))    # 14:00 МСК — дневное окно
NIGHT = calendar.timegm((2026, 8, 17, 0, 0, 0))   # 03:00 МСК — ночь


def st_new(**kw):
    st = {"history": [], "handoff": False, "notify_human": False, "escalation": "",
          "ping_at": 0, "resolved": False, "auto_sent": 0}
    st.update(kw)
    return st


ok = bad = 0


def chk(label, cond):
    global ok, bad
    if cond:
        ok += 1
    else:
        bad += 1
        print("✗", label)


# ⚠ ПЕРЕСЧИТАНО 21.08. Срок дожима перестал быть «пять минут для всех»: он свой у
# каждого чата (12–25 мин, по хэшу) и проходит вентиль вероятности — живой оператор
# пингует 17.3% диалогов, а не каждый. Поэтому тишину берём по сроку ЭТОГО чата, а
# ключи подбираем такие, что вентиль их пропускает: иначе проверялся бы вентиль,
# а не поведение пинга. Обоснование и числа — analysis/test_batch5_ping.py.
КЛЮЧ = "chat-1|m2"                       # вентиль пропускает
ТИШИНА = channel.ping_after(КЛЮЧ) + 60

# 1. тишина дольше срока этого чата → ровно один пинг
st = st_new()
last = {"role": "bot", "text": "Куда подъехать?", "ts": DAY - ТИШИНА, "id": "m2"}
channel._silence_watch(ACC, CHAT, st, last, DAY)
chk("пинг отправлен", len(SENT) == 1 and st["ping_at"] == DAY)
# ⚠ ПЕРЕВЁРНУТО 19.08 (этап 2 программы обучения): пул стал ТРЕМЯ пулами. Голым
# «актуально?» у живых диспетчеров заканчиваются 2029 диалогов — это дешёвая замена
# работе. Теперь пинг несёт то, чего в диалоге не хватает: нет телефона — просим номер
# (+13.2 п.п. к лиду), нет окна — предлагаем время. «Актуально?» остаётся, когда есть всё.
chk("пинг из одного из пулов",
    SENT and SENT[0][1] in (channel.PING_POOL + channel.PING_БЕЗ_НОМЕРА
                            + channel.PING_БЕЗ_ОКНА))
chk("в диалоге без телефона пинг просит номер, а не «актуально?»",
    SENT and SENT[0][1] in channel.PING_БЕЗ_НОМЕРА)

# 2. следующий круг опроса через минуту — второго пинга нет
channel._silence_watch(ACC, CHAT, st, last, DAY + 60)
chk("второго пинга нет", len(SENT) == 1)

# 3. ещё 10 минут тишины после пинга → «Решён», в Авито ничего не уходит
channel._silence_watch(ACC, CHAT, st, last, DAY + 10 * 60)
chk("помечен решённым", st["resolved"] is True and len(SENT) == 1)

# 4. тишина всего 3 минуты → рано, пинга нет
SENT.clear()
st = st_new()
last3 = {"role": "bot", "text": "Куда подъехать?", "ts": DAY - 3 * 60, "id": "m2"}
channel._silence_watch(ACC, CHAT, st, last3, DAY)
chk("3 минуты — рано", not SENT and not st["ping_at"])

# 4б. молчание длиннее срока, но вентиль этот чат не выбрал → тоже тихо
SENT.clear()
мимо = next(k for k in ("chat-1|m%d" % i for i in range(200))
            if not channel.ping_allowed(k))
st = st_new()
channel._silence_watch(ACC, CHAT, st,
                       {"role": "bot", "text": "Куда подъехать?",
                        "ts": DAY - 30 * 60, "id": мимо.split("|")[1]}, DAY)
chk("вентиль не выбрал этот чат — дожима нет", not SENT and not st["ping_at"])

# 5. эскалация в диалоге → пинг не лезет поперёк человека
st = st_new(escalation="status")
channel._silence_watch(ACC, CHAT, st, last, DAY)
chk("эскалация — без пинга", not SENT)

# 6. режим подсказок (auto_reply выкл.) → бот ничего не шлёт
st = st_new()
channel._silence_watch({"id": "a1", "auto_reply": False}, CHAT, st, last, DAY)
chk("подсказки — без пинга", not SENT)

# 7. ночь → без пинга
st = st_new()
last_n = {"role": "bot", "text": "Куда подъехать?", "ts": NIGHT - ТИШИНА, "id": "m2"}
channel._silence_watch(ACC, CHAT, st, last_n, NIGHT)
chk("ночь — без пинга", not SENT)

# 8. ротация: разные чаты получают разные формулировки (по хэшу)
SENT.clear()
texts = set()
прошедшие = [(c, m) for c in range(120) for m in range(1)
              if channel.ping_allowed("chat-%d|m%d" % (c, m))][:12]
for c, m in прошедшие:
    st = st_new()
    ключ = "chat-%d|m%d" % (c, m)
    channel._silence_watch(ACC, {"id": "chat-%d" % c}, st,
                           {"role": "bot", "text": "?",
                            "ts": DAY - channel.ping_after(ключ) - 60,
                            "id": "m%d" % m}, DAY)
texts = {t for _c, t in SENT}
chk("ротация работает (2+ формулировки на 12 чатах)", len(texts) >= 2)

print("=" * 50)
print("Пинг и «Решён»: %d/%d" % (ok, ok + bad))
sys.exit(1 if bad else 0)
