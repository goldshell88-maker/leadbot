# -*- coding: utf-8 -*-
"""Очередь заявок для браузерного расширения (режим crm.autocreate="queue").

Доступ к CRM — только с офисного ПК: бот складывает готовые лиды в очередь,
расширение создаёт заявки и отчитывается. Офлайн: файл очереди во временном
каталоге, сеть не трогается.
"""
import json
import os
import sys
import tempfile

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


tmp = tempfile.mkdtemp()
s._CRM_QUEUE = os.path.join(tmp, "crm_queue.jsonl")

# put → pending
s._crm_queue_put("bt:9261234567:2026-08-16", "bt",
                 {"city": "Иркутск", "phone": "89261234567", "comment": "не морозит"})
s._crm_queue_put("kp:9990000000:2026-08-16", "kp",
                 {"city": "Казань", "phone": "89990000000", "comment": "не включается"})
p = s._crm_queue_pending()
chk("двое в очереди", len(p) == 2)
chk("порядок по времени", p[0]["dir"] == "bt")

# отметка done убирает из pending, журнал хранит id
s._crm_queue_mark("bt:9261234567:2026-08-16", "done", {"id": 777, "by": "extension"})
p = s._crm_queue_pending()
chk("done ушёл из очереди", len(p) == 1 and p[0]["dir"] == "kp")
всё = s._crm_queue_all()
chk("id заявки в журнале", всё["bt:9261234567:2026-08-16"].get("id") == 777)

# failed тоже уходит из pending (человек увидит в журнале)
s._crm_queue_mark("kp:9990000000:2026-08-16", "failed", {"errors": ["город не найден"]})
chk("failed ушёл из очереди", len(s._crm_queue_pending()) == 0)

# захват (16.08: два параллельных опроса создали заявку дважды): очередь
# видна всем, но создавать может только тот, кто захватил ключ
s._crm_queue_put("mnc:9995551122:2026-08-16", "mnc",
                 {"city": "Ангарск", "phone": "89995551122", "comment": "кровля"})
chk("очередь видна без побочек", len(s._crm_queue_pending()) == 1
    and len(s._crm_queue_pending()) == 1)
chk("захват: первый выиграл", s._crm_queue_claim("mnc:9995551122:2026-08-16") is True)
chk("захват: второму отказ", s._crm_queue_claim("mnc:9995551122:2026-08-16") is False)
chk("очередь по-прежнему видна", len(s._crm_queue_pending()) == 1)
s._CRM_LEASE.clear()
chk("захват истёк → можно снова", s._crm_queue_claim("mnc:9995551122:2026-08-16") is True)
chk("чужой ключ не захватить", s._crm_queue_claim("нет-такого") is False)
s._crm_queue_mark("mnc:9995551122:2026-08-16", "done", {"id": 1})
chk("закрытый не захватить", s._crm_queue_claim("mnc:9995551122:2026-08-16") is False)

# токен-гард: пустой в конфиге = дверь закрыта
real_cfg = s.crm_config_ext_token
s.crm_config_ext_token = lambda: ""
chk("пустой токен → отказ", s._crm_ext_token_ok({"X-Crm-Token": "что-то"}) is False)
s.crm_config_ext_token = lambda: "secret-123"
chk("верный токен в X-Crm-Token", s._crm_ext_token_ok({"X-Crm-Token": "secret-123"}))
chk("верный токен в Bearer", s._crm_ext_token_ok({"Authorization": "Bearer secret-123"}))
chk("чужой токен → отказ", s._crm_ext_token_ok({"X-Crm-Token": "wrong"}) is False)
s.crm_config_ext_token = real_cfg

print("=" * 50)
print("Очередь CRM: %d/%d" % (ok, ok + bad))
sys.exit(1 if bad else 0)
