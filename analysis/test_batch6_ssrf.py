# -*- coding: utf-8 -*-
"""ПАЧКА 6 — ЗРЕНИЕ БОТА НЕ ХОДИТ КУДА ПОПАЛО (аудит 22.08).

`_fetch_image_block` вызывал `urllib.request.urlopen(url)` для ЛЮБОГО адреса,
который нашёлся в тексте по шаблону `🖼 https?://…`. Проверки хоста не было
вовсе. Это SSRF: запрос уходит с боевой машины бота, где рядом живут панель
(127.0.0.1:8788), приёмник Jivo (127.0.0.1:8011), PostgreSQL и мост в СПб
(10.10.0.2:8790), а у облака есть служебный адрес 169.254.169.254.

ХУЖЕ ТОГО, ТЕЛО ОТВЕТА УХОДИТ В МОДЕЛЬ. Если байты не похожи ни на JPEG, ни на
PNG, код всё равно помечает их `image/jpeg`, кодирует в base64 и кладёт в запрос
к Anthropic. То есть произвольная внутренняя страница размером до 4 МБ
выгружается наружу, а результат ещё и кэшируется по URL навсегда.

ПОЧЕМУ ЭТО СТАЛО ВАЖНО ИМЕННО СЕЙЧАС. 22.08 LeadChat научился передавать боту
вложения видом «🖼 URL» — до этого путь в связке с LeadChat был мёртв. Адрес
берётся из тела вебхука Авито. А вебхуки Jivo на этой же машине приходят пока
открытым HTTP (находка 3 аудита), то есть подделать сообщение с чужим адресом
может любой, кто перехватил токен пути.

ЧТО СТАВИМ. Список разрешённых хостов, собранный по корпусу (7324 картинки:
7282 с `*.img.avito.st`, 42 с `*.jivo.ru`), плюс отказ на любой адрес, который
резолвится в частную, локальную или служебную сеть, — это закрывает и подмену
DNS у разрешённого хоста.

Запуск: uv run python analysis/test_batch6_ssrf.py   (0 токенов, без сети)
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


def внешний(host):
    """Резолвер-заглушка: разрешённые хосты смотрят наружу."""
    return ["93.158.134.11"]


def внутренний(host):
    return ["127.0.0.1"]


# ── разрешено: настоящие адреса из корпуса ──────────────────────────────────
for u in [
    "https://10.img.avito.st/image/1/1.abc",
    "https://80.img.avito.st/image/1/1.xyz",
    "https://media-sb1.jivo.ru/file/1.jpg",
    "http://60.img.avito.st/image/1/1.q",
]:
    chk("разрешён живой адрес: %s" % u[:46], server._img_url_ok(u, resolve=внешний))

# ── запрещено: внутренние цели ──────────────────────────────────────────────
for u, что in [
    ("http://127.0.0.1:8788/api/dialogs", "своя панель"),
    ("http://127.0.0.1:8011/jivo/секрет", "приёмник Jivo"),
    ("http://10.10.0.2:8790/api/leadchat/answer", "мост в СПб"),
    ("http://169.254.169.254/latest/meta-data/", "метаданные облака"),
    ("http://192.168.1.1/", "локальная сеть"),
    ("http://[::1]:8788/", "loopback по IPv6"),
    ("http://localhost:5432/", "своя база"),
]:
    chk("закрыт %s: %s" % (что, u[:40]), not server._img_url_ok(u, resolve=внешний))

# ── запрещено: чужие хосты, даже похожие ────────────────────────────────────
for u, что in [
    ("https://evil.com/x.jpg", "посторонний хост"),
    ("https://evil-avito.st/x.jpg", "похожий на разрешённый"),
    ("https://avito.st.evil.com/x.jpg", "разрешённый как поддомен чужого"),
    ("https://img.avito.st.attacker.io/1.jpg", "то же с точкой"),
]:
    chk("закрыт %s: %s" % (что, u[:44]), not server._img_url_ok(u, resolve=внешний))

# ── подмена DNS: разрешённый хост смотрит внутрь ────────────────────────────
chk("разрешённый хост, резолвящийся в 127.0.0.1, закрыт",
    not server._img_url_ok("https://10.img.avito.st/1.jpg", resolve=внутренний))

# ── прочие приёмы ───────────────────────────────────────────────────────────
for u, что in [
    ("file:///etc/passwd", "схема file"),
    ("gopher://10.img.avito.st/x", "схема gopher"),
    ("https://user:pass@10.img.avito.st/1.jpg", "логин в адресе"),
    ("https://10.img.avito.st:8788/1.jpg", "нестандартный порт"),
    ("", "пустая строка"),
]:
    chk("закрыт приём «%s»" % что, not server._img_url_ok(u, resolve=внешний))

# ── не картинка — не отправляем в модель ────────────────────────────────────
# Anthropic принимает ровно четыре формата. Всё остальное — либо ошибка, либо
# чужая страница, приехавшая вместо картинки; помечать её «image/jpeg» и
# кодировать в base64 значит выгружать наружу неизвестно что.
for байты, что, годится in [
    (b"\xff\xd8\xff\xe0" + b"0" * 40, "JPEG", True),
    (b"\x89PNG\r\n\x1a\n" + b"0" * 40, "PNG", True),
    (b"GIF89a" + b"0" * 40, "GIF", True),
    (b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 40, "WEBP", True),
    ("<!DOCTYPE html><html><body>секрет".encode("utf-8"), "HTML-страница", False),
    ('{"token": "внутренний ответ"}'.encode("utf-8"), "JSON", False),
    (b"\x7fELF" + b"0" * 40, "исполняемый файл", False),
    (b"", "пусто", False),
]:
    вышло = server._image_media_type(байты)
    chk("формат «%s» %s" % (что, "принят" if годится else "отвергнут"),
        bool(вышло) == годится)

# ── гард стоит НА ПУТИ скачивания, а не рядом ───────────────────────────────
SRC = io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "brain", "server.py"), encoding="utf-8").read()
i = SRC.index("def _fetch_image_block")
тело = SRC[i:i + 1600]
chk("_fetch_image_block спрашивает разрешение до urlopen",
    "_img_url_ok" in тело and тело.index("_img_url_ok") < тело.index("urlopen"))

print("\nИТОГО: %d ок, %d провал" % (ok, bad))
sys.exit(1 if bad else 0)
