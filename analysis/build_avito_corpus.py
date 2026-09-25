# -*- coding: utf-8 -*-
"""ЖИВОЙ КОРПУС АВИТО из боевой базы панели → в общий формат разборов.

ЗАЧЕМ ОТДЕЛЬНО ОТ КОРПУСА JIVO. `analysis/LEARNING/corpus.jsonl` — это 30 526 диалогов
эпохи Jivo: другой канал, другие операторы, другой поток. Бизнес работает на Авито, и
учить бота надо на том, что происходит СЕЙЧАС. Здесь 9 742 живых диалога из боевой базы.

⚠ ЗАМЕТКИ БОТА В ХОДЫ ОПЕРАТОРА НЕ ИДУТ, И ЭТО ГЛАВНОЕ. В базе подсказки лежат
`direction='note'`, а исходящие бота — `sender_type='bot'`. Если их посчитать за ход
живого человека, вся «цена хода» будет измерять бота по самому себе: он предлагает
окно чаще людей, и таблица объявит окно золотом на основании его же реплик.
Люди — это `direction='out'` и `sender_type != 'bot'`. Подсказки складываем отдельным
полем: по ним видно, что бот предлагал и что человек отправил вместо этого.

⚠ ГОРОД БЕРЁМ ИЗ КАРТОЧКИ ОБЪЯВЛЕНИЯ (`item_city_slug`) И ПЕРЕВОДИМ В РУССКОЕ ИМЯ:
бот понимает «Воронеж», а не «voronezh». Пустой город — законное состояние, его в
диалоге 30 %, и это отдельная беда, а не повод выбрасывать диалог.

Запуск: uv run python analysis/build_avito_corpus.py
Офлайн, 0 токенов. Итог: analysis/LEARNING/avito_corpus.jsonl
"""
import io
import json
import os
import re
import sys

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(КОРЕНЬ, "brain"))
ВХОД = os.path.join(КОРЕНЬ, "analysis", "LEARNING", "avito.jsonl")
ВЫХОД = os.path.join(КОРЕНЬ, "analysis", "LEARNING", "avito_corpus.jsonl")
ПОДСКАЗКА = "Подсказка бота: "

# slug → русское имя. Берём из справочника бота, чтобы не заводить второй список.
try:
    import avito_city
    _СПРАВОЧНИК = getattr(avito_city, "CITY_BY_SLUG", None) or {}
except Exception:
    _СПРАВОЧНИК = {}


def имя_города(slug):
    if not slug:
        return ""
    if slug in _СПРАВОЧНИК:
        return _СПРАВОЧНИК[slug]
    # запасной путь: slug вида «nizhniy_novgorod» → «Нижний Новгород» не восстановить,
    # поэтому отдаём как есть — пусть лучше будет видно, что справочник неполон
    return slug


def главное():
    всего = пусто = 0
    с_городом = с_подсказками = 0
    with io.open(ВЫХОД, "w", encoding="utf-8") as out:
        for line in io.open(ВХОД, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            реплики, подсказки = [], []
            for m in (d.get("реплики") or []):
                т = (m.get("текст") or "").strip()
                if not т:
                    continue
                if m.get("вид") == "note":
                    if т.startswith(ПОДСКАЗКА):
                        подсказки.append(т[len(ПОДСКАЗКА):])
                    continue
                роль = "client" if m.get("кто") == "client" else "operator"
                # ⚠ исходящее БОТА человеком не считаем — см. шапку
                if роль == "operator" and m.get("кто") == "bot":
                    continue
                реплики.append({"role": роль, "text": т})
            if not реплики or reple_нет_клиента(реплики):
                пусто += 1
                continue
            всего += 1
            город = имя_города(d.get("город") or "")
            if город:
                с_городом += 1
            if подсказки:
                с_подсказками += 1
            out.write(json.dumps({
                "id": d.get("id"),
                "город": город,
                "город_slug": d.get("город") or "",
                "тема": d.get("тема") or "",
                "статус": d.get("статус") or "",
                "исход": d.get("исход") or "",
                "реплики": реплики,
                "подсказки_бота": подсказки,
            }, ensure_ascii=False) + "\n")
    print("диалогов: %d (пропущено пустых: %d)" % (всего, пусто))
    print("  с городом:      %d (%.1f %%)" % (с_городом, 100.0 * с_городом / max(1, всего)))
    print("  с подсказками бота: %d" % с_подсказками)
    print("итог: %s" % ВЫХОД)
    return 0


def reple_нет_клиента(реплики):
    return not any(m["role"] == "client" for m in реплики)


if __name__ == "__main__":
    sys.exit(главное())
