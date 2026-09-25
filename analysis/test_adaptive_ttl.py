# -*- coding: utf-8 -*-
"""АДАПТИВНЫЙ TTL КЭША (решение заказчика 16.08: «бот сам адаптируется под поток»).

Плотный поток → TTL 5m (запись 1.25× вместо 2×), редкий или неизвестный → 1h
(без холодных перезаписей плейбука). Окна раздельные по моделям. Офлайн.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import server as s  # noqa: E402

ok = bad = 0


def chk(label, cond):
    global ok, bad
    if cond:
        ok += 1
    else:
        bad += 1
        print("✗", label)


real_time = time.time
real_cfg = s.claude_api.config
s.claude_api.config = lambda: {"cache_ttl": "auto"}
try:
    H, S = "haiku-test", "sonnet-test"
    now = [1_000_000.0]
    time.time = lambda: now[0]

    # мало данных → осторожный 1h
    chk("холодный старт → 1h", s._ttl_for(H) == "1h")

    # плотный поток: 12 вызовов раз в минуту → 5m
    for _ in range(12):
        s._ttl_record(H)
        now[0] += 60
    chk("плотный поток → 5m", s._ttl_for(H) == "5m")

    # у другой модели своё окно — по-прежнему 1h
    chk("окна раздельные по моделям", s._ttl_for(S) == "1h")

    # разрыв 6 минут → мгновенный возврат на 1h (кэш бы остыл)
    now[0] += 6 * 60
    chk("разрыв потока → 1h", s._ttl_for(H) == "1h")

    # поток восстановился — снова нужно ПОЛНОЕ окно плотных интервалов
    s._ttl_record(H)
    now[0] += 60
    chk("одного вызова после разрыва мало", s._ttl_for(H) == "1h")
    for _ in range(11):
        s._ttl_record(H)
        now[0] += 60
    chk("окно снова плотное → 5m", s._ttl_for(H) == "5m")

    # жёсткие режимы конфига уважаются
    s.claude_api.config = lambda: {"cache_ttl": "1h"}
    chk("конфиг 1h — без самодеятельности", s._ttl_for(H) == "1h")
    s.claude_api.config = lambda: {"cache_ttl": "5m"}
    chk("конфиг 5m — без самодеятельности", s._ttl_for(H) == "5m")
finally:
    time.time = real_time
    s.claude_api.config = real_cfg
    s._TTL_CALLS.clear()
    s._TTL_MODE.clear()

print("=" * 50)
print("Адаптивный TTL: %d/%d" % (ok, ok + bad))
sys.exit(1 if bad else 0)
