# -*- coding: utf-8 -*-
"""ПАЧКА 1 «защита первой заявки» (Э4 доработки, 21.08.2026, решения владельца 19–21.08).

Каждый тест написан ДО правки и падал на HEAD 9989138+ (доказательство дефекта):
  1. «Отзыв» партнёров 340/7/723 на ЛЮБОМ направлении + 748 (В-001; штраф 500 ₽).
  2. Партнёр 619 → «Прозвон перед выездом» всегда (В-001-пакет).
  3. Хардкод «Белый акк clc.li/MoSZe» изгнан: белая шапка = «🔴БЕЛАЯ ЗАЯВКА🔴» + «Ссылка:»
     из карточки канала (review_url заполнен у всех 8 каналов 20.08).
  4. CustomerRequest[type] ставится сервером: «Гарантия» по словам клиента, иначе «Впервые»
     (историю базы знает только расширение — оно вправе переставить на «Повтор»).
  5. Customer[phone] уходит в маске «+7 XXX-XXX-XXXX» (база другую не принимает).
  6. is_far_ride на КП получает явный дефолт «0» (обязательное поле с пустым дефолтом).

Запуск:  python3 analysis/test_batch1.py       (0 токенов, шлюз не нужен)
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import server  # noqa: E402
import crm     # noqa: E402

ok = bad = 0


def chk(label, cond):
    global ok, bad
    if cond:
        ok += 1
    else:
        bad += 1
        print("✗", label)


# ── 1-2. флаги отзывa и прозвона ──────────────────────────────────────────────
for partner, d, white, ждём in [
    ("7", "kp", False, True),     # главный штрафуемый разрыв: 7 на КП
    ("7", "bt", False, True),
    ("007", "kp", False, True),   # с ведущими нулями
    ("340", "kp", False, True),
    ("723", "kp", False, True),
    ("748", "bt", False, True),   # 748 в коде не было вовсе
    ("748", "kp", False, True),
    ("100", "bt", False, False),  # чужой партнёр — отзыва нет
    ("", "bt", True, True),       # white сам по себе даёт отзыв
]:
    chk("review(partner=%s, d=%s)==%s" % (partner, d, ждём),
        server._review_flag(partner, white, d) == ждём)

for partner, текст, ждём in [
    ("619", "стиральная машина не крутит", True),      # 619 всегда
    ("0619", "не сливает", True),
    ("7", "позвоните перед выездом пожалуйста", True),  # по тексту — как раньше
    ("7", "не сливает", False),
    ("100", "приезжайте", False),
]:
    chk("call_before(partner=%s)==%s" % (partner, ждём),
        server._callbefore_flag(partner, текст) == ждём)

# ── 3. белая шапка без хардкода ──────────────────────────────────────────────
src = io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "brain", "server.py"), encoding="utf-8").read()
chk("хардкод clc.li/MoSZe изгнан из server.py", "clc.li/MoSZe" not in src)
шапка = server._white_lines("https://www.avito.ru/brands/komphelper")
chk("белая шапка: 🔴БЕЛАЯ ЗАЯВКА🔴", шапка and шапка[0] == "🔴БЕЛАЯ ЗАЯВКА🔴")
chk("белая шапка: Ссылка: из карточки канала",
    len(шапка) > 1 and шапка[1] == "Ссылка: https://www.avito.ru/brands/komphelper")
chk("белая шапка без ссылки — только заголовок", server._white_lines("") == ["🔴БЕЛАЯ ЗАЯВКА🔴"])

# ── 4-6. поля заявки (фикстура формы, сеть не нужна) ─────────────────────────
ФИКСТУРЫ = [os.path.expanduser(p) for p in (
    "~/Desktop/ДОРАБОТКА/ЗНАНИЯ-2.0/исходники/КП - Создание заявки.html",
    "~/Desktop/Work/База/КП - Создание заявки.html")]
ф = next((p for p in ФИКСТУРЫ if os.path.exists(p)), None)
if not ф:
    print("фикстуры формы нет на этой машине — блок полей пропущен (SKIP)")
else:
    form = crm.parse_form(io.open(ф, encoding="utf-8", errors="replace").read())
    lead = {"city": "Пятигорск", "phone": "89001112241", "name": "Тест",
            "comment": "тест", "repeat_hint": ""}
    p1 = crm.build_patch(form, lead, appliance_options=[("257", "Компьютер", False)], direction="kp")
    chk("type: дефолт «Впервые» (10)", p1.get("CustomerRequest[type]") == "10")
    chk("phone: маска +7 900-111-2241", p1.get("Customer[phone]") == "+7 900-111-2241")
    chk("is_far_ride: явный «0» на КП", p1.get("CustomerRequest[is_far_ride]") == "0")
    lead2 = dict(lead, repeat_hint="гарантия")
    p2 = crm.build_patch(form, lead2, appliance_options=[("257", "Компьютер", False)], direction="kp")
    chk("type: «Гарантия» (30) по словам клиента", p2.get("CustomerRequest[type]") == "30")
    p3 = crm.build_patch(form, lead, appliance_options=[("257", "Компьютер", False)], direction="bt")
    chk("is_far_ride не трогаем вне КП", "CustomerRequest[is_far_ride]" not in p3)

print("\nИТОГО: %d ок, %d провал" % (ok, bad))
sys.exit(1 if bad else 0)
