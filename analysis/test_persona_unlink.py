# -*- coding: utf-8 -*-
"""НЕСВЯЗЫВАЕМОСТЬ АККАУНТОВ (требование владельца 17.08).

Клиент рассылает ОДИН текст на десять аккаунтов — ответы обязаны быть
разными: одинаковый дословный ответ мгновенно связывает аккаунты между
собой. Проверяем офлайн-механику: ключ кэша ответов расходится по персоне,
персоны раздаются детерминированно и стабильно.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import server  # noqa: E402
import prompt  # noqa: E402

ok = bad = 0


def chk(label, cond):
    global ok, bad
    if cond:
        ok += 1
    else:
        bad += 1
        print("✗", label)


msgs = [{"role": "client", "text": "Здравствуйте, сколько стоит ремонт стиральной машины?"}]

# ключ кэша: одинаковый вопрос, разные персоны → разные записи кэша
ключи = set()
for pid in ("p1", "p2", "p3", "p4", "p5", "p6", "p7", ""):
    kr = {"city": "Москва", "direction": "bt", "persona": pid, "title": "Ремонт стиральных машин"}
    ключи.add(server._answer_key(msgs, kr))
chk("8 персон → 8 разных ключей кэша", len(ключи) == 8)

# у каждой персоны есть манера, и блок непустой
for pid in ("p1", "p2", "p3", "p4", "p5", "p6", "p7"):
    chk("манера %s задана" % pid, bool(prompt.persona_style(pid)))
chk("пустая персона → пустой блок (не ломаем кэш промпта)", prompt.persona_style("") == "")

print("=" * 50)
print("Несвязываемость: %d/%d" % (ok, ok + bad))
sys.exit(1 if bad else 0)
