# -*- coding: utf-8 -*-
"""ПАЧКА 7 — ПАМЯТЬ ПРОЦЕССА НЕ РАСТЁТ БЕСКОНЕЧНО (аудит 22.08, шаг 3).

Машина бота — 2 ядра и 1.9 ГБ, свободно около 1.4 ГБ. В процессе живут четыре
словаря, и ни у одного не было потолка:

    _IMG_CACHE = {}   # url -> image-блок, «успех помним НАВСЕГДА»
    _IMG_FAIL  = {}   # url -> (когда сорвалось, сколько раз)
    _DIALOGS   = {}   # dialog_id -> состояние
    _ANSWER_CACHE = {}

Опаснее всех первый. PIL на боевой машине НЕ УСТАНОВЛЕН — проверено на сервере, —
значит `_downscale` возвращает исходные байты, и в кэш ложится картинка целиком:
до 3.8 МБ сырыми, до 5.1 МБ в base64. **280 фотографий — и памяти нет.**
Процесс убивает OOM, канал умирает молча, а узнают об этом по тишине.

До 22.08 путь был почти мёртв: LeadChat вложений боту не передавал вовсе. Сегодня
утром я это включил (`6a5c58b`) — то есть счётчик пошёл именно сейчас.

ЧТО СТАВИМ. Потолок и по числу записей, и по суммарным байтам, вытеснение самого
старого (LRU). Кэш обязан оставаться кэшем: ускорять повтор, а не хранить всё.

Запуск: uv run python analysis/test_batch7_memory.py   (0 токенов, без сети)
"""
import io
import os
import sys

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


def блок(мб):
    """Похожий на настоящий image-блок заданного размера."""
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": "A" * int(мб * 1024 * 1024)}}


# ── потолки объявлены и разумны для машины на 1.9 ГБ ────────────────────────
chk("потолок числа записей объявлен", hasattr(server, "_IMG_CACHE_MAX_ITEMS"))
chk("потолок байтов объявлен", hasattr(server, "_IMG_CACHE_MAX_BYTES"))
chk("потолок байтов помещается в память машины: %d МБ"
    % (server._IMG_CACHE_MAX_BYTES / 1024 / 1024),
    0 < server._IMG_CACHE_MAX_BYTES <= 200 * 1024 * 1024)
chk("потолок числа записей осмыслен: %d" % server._IMG_CACHE_MAX_ITEMS,
    0 < server._IMG_CACHE_MAX_ITEMS <= 500)

# ── вытеснение по числу ─────────────────────────────────────────────────────
server._IMG_CACHE.clear()
for i in range(server._IMG_CACHE_MAX_ITEMS + 20):
    server._img_cache_put("https://10.img.avito.st/%d.jpg" % i, блок(0.01))
chk("число записей не превысило потолок: %d" % len(server._IMG_CACHE),
    len(server._IMG_CACHE) <= server._IMG_CACHE_MAX_ITEMS)
chk("вытеснён самый старый, а не самый свежий",
    "https://10.img.avito.st/0.jpg" not in server._IMG_CACHE
    and "https://10.img.avito.st/%d.jpg" % (server._IMG_CACHE_MAX_ITEMS + 19) in server._IMG_CACHE)

# ── вытеснение по суммарным байтам ──────────────────────────────────────────
server._IMG_CACHE.clear()
крупная = server._IMG_CACHE_MAX_BYTES / 1024 / 1024 / 4          # четверть бюджета
for i in range(10):
    server._img_cache_put("https://10.img.avito.st/big%d.jpg" % i, блок(крупная))
занято = sum(server._img_block_size(b) for b in server._IMG_CACHE.values())
chk("суммарный объём в бюджете: %.0f МБ при потолке %.0f МБ"
    % (занято / 1024 / 1024, server._IMG_CACHE_MAX_BYTES / 1024 / 1024),
    занято <= server._IMG_CACHE_MAX_BYTES)
chk("при вытеснении по байтам кэш не опустел целиком", len(server._IMG_CACHE) >= 2)

# ── попадание освежает запись (иначе это не LRU, а очередь) ─────────────────
server._IMG_CACHE.clear()
for i in range(server._IMG_CACHE_MAX_ITEMS):
    server._img_cache_put("https://10.img.avito.st/l%d.jpg" % i, блок(0.01))
server._img_cache_get("https://10.img.avito.st/l0.jpg")           # трогаем самую старую
server._img_cache_put("https://10.img.avito.st/новая.jpg", блок(0.01))
chk("тронутая запись пережила вытеснение",
    "https://10.img.avito.st/l0.jpg" in server._IMG_CACHE)

# ── журнал неудач тоже с потолком ───────────────────────────────────────────
chk("потолок журнала неудач объявлен", hasattr(server, "_IMG_FAIL_MAX_ITEMS"))
server._IMG_FAIL.clear()
for i in range(server._IMG_FAIL_MAX_ITEMS + 50):
    server._img_fail_put("https://10.img.avito.st/f%d.jpg" % i)
chk("журнал неудач не превысил потолок: %d" % len(server._IMG_FAIL),
    len(server._IMG_FAIL) <= server._IMG_FAIL_MAX_ITEMS)

# ── скачивание пользуется именно этими воротами ─────────────────────────────
SRC = io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "brain", "server.py"), encoding="utf-8").read()
i = SRC.index("def _fetch_image_block")
тело = SRC[i:i + 2200]
chk("_fetch_image_block кладёт через ворота с потолком", "_img_cache_put" in тело)
chk("_fetch_image_block читает через ворота", "_img_cache_get" in тело)
j = SRC.index("def _img_cache_put")
ворота = SRC[j:SRC.index("def _img_fail_put")]
chk("запись в словарь есть только внутри ворот",
    SRC.count("_IMG_CACHE[url] =") == 1 and "_IMG_CACHE[url] =" in ворота)
chk("журнал неудач тоже пишется только через ворота",
    SRC.count("_IMG_FAIL[url] =") == 1)

print("\nИТОГО: %d ок, %d провал" % (ok, bad))
sys.exit(1 if bad else 0)
