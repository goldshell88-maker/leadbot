# -*- coding: utf-8 -*-
"""Справочник филиалов КП после выгрузки CRM заказчика 15.08.2026.

Сверка select city_id с filials.py дала: 4 города переехали из филиалов в
спутники, 7 новых спутников, 7 городов с пометкой «-» (трактуем осторожно —
сверка, не отказ). Карта по-прежнему главнее справочника.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import filials  # noqa: E402
import territory  # noqa: E402

ok = bad = 0


def chk(label, cond):
    global ok, bad
    if cond:
        ok += 1
    else:
        bad += 1
        print("✗", label)


def prof(город):
    return territory.slot_profile(город, False, territory.map_profile(город, "", "kp"),
                                  {"text": "", "direction": "kp", "topic": ""})


# переехавшие из филиалов в спутники (CRM 15.08)
for город, родитель in [("Муром", "Владимир"), ("Коммунарка", "Подольск"),
                        ("Выборг", "Санкт-Петербург"), ("Домодедово", "Видное")]:
    chk("%s — спутник %s" % (город, родитель),
        filials.satellite_of(город, "kp") == родитель)

# новые спутники
for город, родитель in [("Гусь-Хрустальный", "Владимир"), ("Железнодорожный", "Реутов"),
                        ("Павловский Посад", "Электросталь-Ногинск")]:
    chk("%s — спутник %s" % (город, родитель),
        filials.satellite_of(город, "kp") == родитель)
p = prof("Муром")
chk("Муром: 2-2.5 часа без сверки",
    p["lead"].startswith("два") and not p["needs_approval"])

# города с пометкой «-»: сверка, не отказ
for город in ["Талнах", "Феодосия", "Комсомольск-на-Амуре", "Димитровград"]:
    p = prof(город)
    chk("%s: «-» → согласование" % город, p["needs_approval"] is True)

# обычные филиалы не задеты
p = prof("Казань")
chk("Казань: черта города, час-полтора",
    p["lead"] == "час-полтора" and not p["needs_approval"])

# карта главнее справочника: у Адлера свой контур
p = prof("Адлер")
chk("Адлер: зона решается картой", "карт" in (p.get("zone") or ""))

# Коммунарка: и CRM, и сам контур карты («Коммунарка (Подольск)») зовут её
# подольской — спутниковые правила побеждают контур черты города
p = prof("Коммунарка")
chk("Коммунарка: спутник по справочнику даже в контуре карты",
    "спутник" in (p.get("zone") or "") and p["lead"].startswith("два"))

# ── настройка городов (data/city_overrides.json, просьба заказчика 15.08) ──
import json
import pathlib

_OVR = pathlib.Path(__file__).resolve().parent.parent / "data" / "city_overrides.json"
_прежнее = _OVR.read_text(encoding="utf-8") if _OVR.exists() else None
try:
    _OVR.write_text(json.dumps({
        "kp": {"казань": {"status": "closed"},
               "тестоград": {"status": "satellite", "parent": "Уфа"}},
    }, ensure_ascii=False), encoding="utf-8")
    p = prof("Казань")
    chk("оверрайд: Казань закрыта → сверка", p["needs_approval"] is True)
    chk("оверрайд: Тестоград — спутник Уфы",
        filials.satellite_of("Тестоград", "kp") == "Уфа")
finally:
    if _прежнее is None:
        _OVR.write_text("{}", encoding="utf-8")
    else:
        _OVR.write_text(_прежнее, encoding="utf-8")
p = prof("Казань")
chk("оверрайд снят: Казань снова черта города", p["needs_approval"] is False)

print("=" * 50)
print("Филиалы КП: %d/%d" % (ok, ok + bad))
sys.exit(1 if bad else 0)
