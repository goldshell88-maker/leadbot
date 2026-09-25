# -*- coding: utf-8 -*-
"""Чистка базы примеров RAG от ПДн живых клиентов (Э4, пачка 4, 21.08).

brain/rag_examples.json собран из настоящих диалогов, и rag.note_for подставляет
примеры в system-промпт КАЖДОГО хода. Значит любой телефон или адрес, попавший
в пример, уезжает в модель при каждом обращении — это ПДн третьих лиц.

Скрипт прогоняет поля q/a через rag.mask_pii и переписывает файл на месте.
Запускать после каждого пополнения базы примеров; сама выдача тоже маскируется
(двойной рубеж — база пополняется, и один рубеж однажды забудут).

Запуск: uv run python analysis/mask_rag_pii.py [--dry]
"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(КОРЕНЬ, "brain"))
import rag  # noqa: E402

ПУТЬ = os.path.join(КОРЕНЬ, "brain", "rag_examples.json")
сухой = "--dry" in sys.argv

data = json.load(io.open(ПУТЬ, encoding="utf-8"))
примеры = data if isinstance(data, list) else (data.get("examples") or data.get("pairs"))
if примеры is None:
    print("не нашёл списка примеров в %s" % ПУТЬ)
    sys.exit(1)

тронуто = 0
образцы = []
for ex in примеры:
    for поле in ("q", "a"):
        было = ex.get(поле)
        if not было:
            continue
        стало = rag.mask_pii(было)
        if стало != было:
            тронуто += 1
            if len(образцы) < 8:
                образцы.append((было[:70], стало[:70]))
            if not сухой:
                ex[поле] = стало

for было, стало in образцы:
    print("  %-70s\n→ %-70s" % (было, стало))
print("\nполей замаскировано: %d из %d примеров" % (тронуто, len(примеры)))

if сухой:
    print("сухой прогон — файл не тронут")
else:
    io.open(ПУТЬ, "w", encoding="utf-8").write(
        json.dumps(data, ensure_ascii=False, indent=2))
    print("записано: %s" % ПУТЬ)
