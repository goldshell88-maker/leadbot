# -*- coding: utf-8 -*-
"""ПАЧКА 4.1б — ПДн НЕ ВОЗВРАЩАЮТСЯ ПРИ ПЕРЕСБОРКЕ ПУЛА (Э4, 22.08).

Выкатка 22.08 обнажила дыру в собственной же правке. База примеров вычищена и
маскируется на выдаче — но СОБИРАЕТСЯ она скриптом analysis/build_rag_pool.py
прямо из живых диалогов, и он про маску ничего не знал. Первая же пересборка
пула вернула бы в brain/rag_examples.json и телефоны, и адреса, а заметил бы это
только тот, кто снова догадался бы прогнать test_batch4_privacy.

Рубежа теперь три, и это не перестраховка, а разные точки отказа:
  1. сборка пула — чтобы грязь не попадала в файл вообще (этот тест);
  2. файл в репозитории — чтобы уже попавшая была вычищена (test_batch4_privacy);
  3. выдача note_for — чтобы пережить и первые два, если их однажды обойдут.

Запуск: uv run python analysis/test_batch4_privacy_build.py   (0 токенов)
"""
import io
import os
import re
import sys

ФД = sys.stdout.fileno()


def скажи(s=""):
    поток = io.TextIOWrapper(open(ФД, "wb", closefd=False), encoding="utf-8")
    поток.write(s + "\n")
    поток.flush()
    поток.detach()


КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(КОРЕНЬ, "brain"))
sys.path.insert(0, os.path.join(КОРЕНЬ, "analysis"))

ok = bad = 0


def chk(label, cond):
    global ok, bad
    if cond:
        ok += 1
    else:
        bad += 1
        скажи("✗ " + label)


ИСХОДНИК = io.open(os.path.join(КОРЕНЬ, "analysis", "build_rag_pool.py"), encoding="utf-8").read()

chk("сборщик знает про маску ПДн", "mask_pii" in ИСХОДНИК)
chk("маска стоит перед записью файла",
    ИСХОДНИК.index("mask_pii") < ИСХОДНИК.rindex("json.dump"))

import build_rag_pool  # noqa: E402

chk("у сборщика есть отдельная функция чистки", hasattr(build_rag_pool, "без_пдн"))

ТЕЛЕФОН = re.compile(r"(?<!\d)\d{10,11}(?!\d)")
АДРЕС = re.compile(
    r"(?<![а-яёА-ЯЁa-zA-Z])"
    r"(?:ул\.?|улиц\w*|пр-т|проспект|пер\.?|переул\w*|бульвар|шоссе|мкр|микрорайон|"
    r"д\.?|дом|кв\.?|квартир\w*|подъезд|подьезд|под\.|пд|этаж)\s*\.?\s*\d{1,4}",
    re.IGNORECASE)

ГРЯЗЬ = [
    {"q": "мой номер 9001112242, приезжайте", "a": "хорошо, буду", "class": "вход", "src": "corpus"},
    {"q": "адрес Цветочная 12 кв 5, подъезд 2", "a": "записал, до встречи", "class": "адрес", "src": "corpus"},
    {"q": "стиралка не сливает", "a": "звоните 89001112243", "class": "вход", "src": "gold"},
]
чисто = build_rag_pool.без_пдн(ГРЯЗЬ)

chk("ни одна пара не потеряна", len(чисто) == len(ГРЯЗЬ))
for i, п in enumerate(чисто):
    поле = (п.get("q") or "") + " " + (п.get("a") or "")
    chk("пара %d: телефонов нет" % i, not ТЕЛЕФОН.search(поле))
    chk("пара %d: адресов нет" % i, not АДРЕС.search(поле))
chk("суть обращения сохранена", "стиралка не сливает" in (чисто[2].get("q") or ""))
chk("служебные поля не тронуты",
    чисто[0].get("class") == "вход" and чисто[0].get("src") == "corpus")

скажи("\nИТОГО: %d ок, %d провал" % (ok, bad))
sys.exit(1 if bad else 0)
