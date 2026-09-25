# -*- coding: utf-8 -*-
"""ВЫГРУЗКА ГРАНИЦ ФИЛИАЛОВ из конструкторных карт Яндекса в brain/filial_zones.json.

Заказчик ведёт территории филиалов в «Конструкторе карт» Яндекса — отдельная карта на
каждое направление (КП / МНЧ / БТ). Ссылка вида
    https://yandex.ru/maps/?...&um=constructor%3A<ХЕШ>&...
Виджет этой карты отдаёт всю геометрию обычным JSON внутри тега
    <script type="application/json" class="config-view">
— оттуда и берём полигоны, без ключей и API.

⚠ Яндекс отдаёт координаты как [долгота, широта]; мы храним [широта, долгота].

Запуск:
    py analysis\\fetch_filial_zones.py            — выгрузить все три карты
    py analysis\\fetch_filial_zones.py --dump     — только показать, что на картах (без записи)
"""
import io
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import paths  # noqa: E402

OUT = os.path.join(paths.BRAIN, "filial_zones.json")

MAPS = {
    "kp": "3a441478bf388f1b547feea234423b58044ed891044d8420a95175a4f37d3d71",
    "mnc": "5334b1eac64665e4dbf0b1a57d801a0db433c7da380733a4932291434303d388",
    "bt": "1d14f05bb28add9e89923b4a5ed00d06247b78aa8bc0bed89ac0a934bc07403f",
}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126", "Accept-Language": "ru"}


def fetch(map_hash, timeout=40):
    url = "https://yandex.ru/map-widget/v1/?um=constructor%3A" + map_hash + "&lang=ru_RU"
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def payload(html):
    """JSON-конфиг виджета — там вся геометрия карты."""
    key = '<script type="application/json" class="config-view">'
    i = html.find(key)
    if i < 0:
        raise RuntimeError("не нашёл config-view в ответе виджета")
    i += len(key)
    j = html.find("</script>", i)
    return json.loads(html[i:j])


def walk(node, out):
    """Рекурсивно собираем всё, у чего есть geometry с координатами."""
    if isinstance(node, dict):
        g = node.get("geometry")
        if isinstance(g, dict) and g.get("coordinates") and g.get("type"):
            out.append({"kind": node.get("type") or "", "title": (node.get("title") or "").strip(),
                        "gtype": g.get("type"), "coords": g.get("coordinates")})
        for v in node.values():
            walk(v, out)
    elif isinstance(node, list):
        for v in node:
            walk(v, out)
    return out


def rings(gtype, coords):
    """Приводим геометрию к списку контуров [[шир, долг], ...] (Яндекс даёт [долг, шир])."""
    res = []
    if gtype == "Polygon":
        for ring in coords:
            if ring and isinstance(ring[0], (list, tuple)):
                res.append([[p[1], p[0]] for p in ring if isinstance(p, (list, tuple)) and len(p) >= 2])
    elif gtype == "LineString":
        res.append([[p[1], p[0]] for p in coords if isinstance(p, (list, tuple)) and len(p) >= 2])
    elif gtype == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                res.append([[p[1], p[0]] for p in ring if isinstance(p, (list, tuple)) and len(p) >= 2])
    return [r for r in res if len(r) >= 3]


def when_updated(cfg):
    """Когда владелец последний раз правил эту карту (`userMap.lastUpdated` из виджета).

    ⚠ ЕДИНСТВЕННЫЙ ДЕШЁВЫЙ ПРИЗНАК СВЕЖЕСТИ. Ни ETag, ни Last-Modified виджет не отдаёт, а
    сравнивать геометрию значит каждый раз качать все три карты. По этому полю видно, что
    карта изменилась, до разбора — и можно не трогать файл, если ничего не менялось.
    """
    найдено = []

    def _ходить(n):
        if isinstance(n, dict):
            for k, v in n.items():
                if k == "lastUpdated" and isinstance(v, (str, int, float)):
                    найдено.append(str(v))
                _ходить(v)
        elif isinstance(n, list):
            for v in n:
                _ходить(v)
    _ходить(cfg)
    return найдено[0] if найдено else ""


def collect(direction, map_hash, dump=False):
    _cfg = payload(fetch(map_hash))
    _ПРАВЛЕНО[direction] = when_updated(_cfg)
    objs = walk(_cfg, [])
    polys, lines = [], []
    for o in objs:
        for ring in rings(o["gtype"], o["coords"]):
            (polys if o["gtype"] in ("Polygon", "MultiPolygon") else lines).append(
                {"title": o["title"], "ring": ring})
    if dump:
        print("\n=== %s: объектов %d (полигонов %d, линий %d)" % (direction.upper(), len(objs), len(polys), len(lines)))
        from collections import Counter
        c = Counter(p["title"] for p in polys)
        for t, n in c.most_common(60):
            print("   полигон %-52s x%d" % ((t or "(без названия)")[:52], n))
        c2 = Counter(l["title"] for l in lines if l["title"])
        for t, n in list(c2.most_common(15)):
            print("   линия   %-52s x%d" % (t[:52], n))
    return polys, lines


# ── РАЗБОР НАЗВАНИЙ КОНТУРОВ ─────────────────────────────────────────────────────
# Заказчик записывает правило приёма прямо в название полигона, например:
#   «СОЧИ ЗА 1,5 ЧАСА», «СЕРПУХОВ (ЗА 2-2.5)», «Серпухов (ЗА 3 Часа)», «ПО СОГЛАСОВАНИЮ»,
#   «Зеленоград (МСК)», «Воронеж, принимаем по согласованию с городом».
# Значит время приёма и признак московской сети берём ИЗ САМОЙ КАРТЫ, а не угадываем.
_SOGLAS_RX = re.compile(r"соглас", re.I)
_MSK_RX = re.compile(r"\(\s*мск\s*\)", re.I)
# ⚠ «ЗА 3 - 3.5 ЧАСА» ЧЕРЕЗ ДЕФИС ТОЖЕ ЗНАЧИТ ТРИ ЧАСА: без этого самая медленная территория
# получала бы самое быстрое обещание — умолчание «час-полтора».
_H3_RX = re.compile(r"за\s*3\s*[\s,.\-–]*(?:3[,.]5\s*)?час|3-3[,.]5", re.I)
# ⚠ «ЗА 2 ЧАСА» РОВНО — ОТДЕЛЬНАЯ ФОРМА, И ЕЁ НЕ БЫЛО. Метка владельца «ПРИНИМАЕМ ЗА 2 ЧАСА»
# не подходила ни под «2-2.5», ни под «2,5», и контур получал умолчание «час-полтора» — то есть
# бот обещал приезд ВДВОЕ быстрее, чем филиал едет. На карте КП таких меток две: внутри контуров
# «Видное (МСК)» и «Одинцово (МСК)» (разбор 27.08). Ошибаться тут можно только в сторону
# осторожности, поэтому ровные два часа читаем как ближайшую медленную ступень словаря.
_H25_RX = re.compile(r"за\s*2[\s,.\-–]*2[,.]5|за\s*2[,.]5\s*час|2-2[,.]5|за\s*2\s*час", re.I)
_H15_RX = re.compile(r"за\s*1[,.]5\s*час|1-1[,.]5", re.I)


# ── МЕТКИ-ТОЧКИ (их НЕТ в виджете, только на странице карт) ─────────────────────
# ⚠ НАХОДКА 01.08.2026. Виджет отдаёт только полигоны и линии, а заказчик пишет время приёма
# ещё и МЕТКАМИ внутри контуров: «ПРИНИМАЕМ ЗА 2,5 ЧАСА», «КЛИН ЗА 2,5 ЧАСА», «ВЫБОРГ ЗА 3 ЧАСА».
# В названии самого контура времени при этом нет («Мытищи (МСК)»), и парсер ставил ему
# «час-полтора» по умолчанию. Итог: по 20 контурам бот обещал час-полтора там, где филиал
# едет 2-2.5 часа, — Мытищи, Королёв, Электросталь, Ногинск, Раменское, Жуковский, Видное,
# Одинцово, Красногорск, Клин, Кронштадт, Гатчина, а Выборг вообще за 3 часа.
PAGE = "https://yandex.ru/maps/?mode=usermaps&source=constructorLink&um=constructor%3A"
_MARK_RX = re.compile(r'\{"title":"((?:[^"\\]|\\.)*)","subtitle":"[^"]*","isTextInTitle":\w+,'
                      r'"zIndex":\d+,"type":"placemark","coordinates":\[([-\d.]+),([-\d.]+)\]')
_MARK_TIME_RX = re.compile(r"час", re.I)
# насколько «медленно» — чем больше, тем позже приезд; сравниваем, чтобы взять САМОЕ медленное
_LEAD_RANK = {"час-полтора": 1, "два, два с половиной часа": 2, "три часа": 3}


def fetch_page(map_hash, timeout=60):
    """HTML страницы карт: там, в отличие от виджета, лежат ещё и метки-точки."""
    req = urllib.request.Request(PAGE + map_hash, headers=dict(
        UA, **{"Accept": "text/html,application/xhtml+xml"}))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def marks(html):
    """[(название, широта, долгота)] — метки заказчика. Яндекс отдаёт [долгота, широта]."""
    seen, res = set(), []
    for m in _MARK_RX.finditer(html):
        try:
            title = json.loads('"' + m.group(1) + '"').strip()
        except Exception:
            title = m.group(1).strip()
        lon, lat = float(m.group(2)), float(m.group(3))
        key = (title, round(lat, 5), round(lon, 5))
        if key in seen:
            continue
        seen.add(key)
        res.append((title, lat, lon))
    return res


_PAGE_LINE_RX = re.compile(r'"type":"LineString","coordinates":(\[\[[-\d.,\[\]]+\]\])\},'
                           r'"title":"((?:[^"\\]|\\.)*)"')


def page_lines(html):
    """Линии со СТРАНИЦЫ карт. Виджет часть из них отдаёт ПУСТЫМИ (0 точек) — так терялись
    «Граница Клин - Зеленоград» и «Граница КРОНШТАДТ-СБП1». Право приёма они не задают
    (его задаёт полигон), но разметку заказчика надо хранить целиком."""
    res = []
    for m in _PAGE_LINE_RX.finditer(html):
        try:
            pts = json.loads(m.group(1))
            title = json.loads('"' + m.group(2) + '"').strip()
        except Exception:
            continue
        ring = [[p[1], p[0]] for p in pts if isinstance(p, list) and len(p) >= 2]
        if ring:
            res.append({"title": title, "ring": ring})
    return res


def _in_ring(lat, lon, ring):
    """Точка внутри контура (ray casting). Дубль territory.point_in_ring — выгрузка не должна
    зависеть от рантайма бота."""
    inside = False
    n = len(ring)
    for i in range(n):
        y1, x1 = ring[i][0], ring[i][1]
        y2, x2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            xin = (x2 - x1) * (lat - y1) / ((y2 - y1) or 1e-12) + x1
            if lon < xin:
                inside = not inside
    return inside


def apply_marks(areas, marks_by_dir):
    """Время из МЕТКИ внутри контура важнее умолчания по названию контура.

    Если меток несколько — берём САМУЮ МЕДЛЕННУЮ: пообещать быстрее, чем филиал доедет, дороже,
    чем пообещать осторожнее. Метки «ПО СОГЛАСОВАНИЮ» время не задают и зону НЕ переключают:
    одна точка не описывает область, а перевод всего контура в согласование убил бы живые лиды
    (в Одинцово такая метка стоит рядом с «ПРИНИМАЕМ ЗА 2 ЧАСА»).

    ⚠ МЕТКА, НАЗЫВАЮЩАЯ ГОРОД, НА ВЕСЬ КОНТУР НЕ РАСПРОСТРАНЯЕТСЯ. «КРОНШТАДТ ЗА 2,5 ЧАСА»
    лежит внутри огромного контура «СПБ 1», и наивное правило замедлило бы ВЕСЬ Петербург до
    2.5 часов; «КЛИН ЗА 2,5 ЧАСА» — внутри контура Зеленограда. Такие метки задают правило
    СВОЕМУ городу и возвращаются отдельным списком city_rules. На контур действуют только
    безымянные метки: «ПРИНИМАЕМ ЗА 2,5 ЧАСА», «ВСЕ ПРИНИМАЕМ ЗА 2,5 ЧАСА».
    """
    changed, city_rules = [], {}
    for direction, mk in marks_by_dir.items():
        for title, lat, lon in mk:
            if not _MARK_TIME_RX.search(title):
                continue
            city = _mark_city(title)
            _c, _z, lead, early, _m = parse_title(title)
            if not city:
                continue
            cur = city_rules.get((direction, city))
            if cur is None or _LEAD_RANK.get(lead, 0) > _LEAD_RANK.get(cur["lead"], 0):
                city_rules[(direction, city)] = {"direction": direction, "city": city,
                                                 "lead": lead, "earliest": early, "mark": title}
    for a in areas:
        best = None
        for title, lat, lon in marks_by_dir.get(a["direction"], []):
            if not _MARK_TIME_RX.search(title):
                continue
            if not _in_ring(lat, lon, a.get("ring") or []):
                continue
            city = _mark_city(title)
            _c, _z, lead, early, _m = parse_title(title)
            # метка про ДРУГОЙ город — это правило того города, а не всего контура
            if city and _norm_city(city) != _norm_city(a.get("city")):
                continue
            if best is None or _LEAD_RANK.get(lead, 0) > _LEAD_RANK.get(best[0], 0):
                best = (lead, early, title)
        if best and best[0] != a["lead"]:
            changed.append((a["direction"], a["name"], a["lead"], best[0], best[2]))
            a["lead"], a["earliest"] = best[0], best[1]
            if _LEAD_RANK.get(best[0], 1) > 1 and a["zone"] == "city":
                a["zone"] = "satellite"      # приём за 2+ часа — это не «черта города»
    return changed, sorted(city_rules.values(), key=lambda x: (x["direction"], x["city"]))


def _norm_city(s):
    return (s or "").strip().lower().replace("ё", "е")


# Служебные слова в названии МЕТКИ. Если после снятия времени не осталось ничего, кроме них,
# метка БЕЗЫМЯННАЯ и относится ко всему контуру: «ВСЕ ПРИНИМАЕМ ЗА 2,5 ЧАСА»,
# «ПРИЁМ ЗАЯВОК В ТЕЧЕНИИ 2-2,5 ЧАСОВ». Без этого «Все» и «Приём Явок» становились «городами»,
# и правка не доходила до контуров Красногорска, Одинцово и Королёва.
# Сверяем по ОСНОВАМ, а не по точным формам: parse_title обрезает окончания («ТЕЧЕНИИ» → «Течен»),
# и список точных слов приходилось бы дополнять после каждой правки заказчика.
# ⚠ КОРОТКИЕ СЛУЖЕБНЫЕ СЛОВА СРАВНИВАЕМ ЦЕЛИКОМ, длинные — по основе. Иначе односимвольная
# основа «в» съедала «Выборг», и метка «ВЫБОРГ ЗА 3 ЧАСА» считалась безымянной.
_MARK_STOP_EXACT = {"в", "и", "на", "по", "все", "всё"}
_MARK_STOP_STEM = ("прин", "прием", "приём", "заяв", "теч", "час", "соглас", "работ")


def _mark_city(title):
    """Город, названный в метке, либо '' если метка безымянная (относится ко всему контуру)."""
    city = parse_title(title)[0]
    words = [w for w in re.split(r"[\s,;()]+", _norm_city(city)) if w]
    words = [w for w in words if re.search(r"[а-яё]", w)]   # «2-2» и прочие хвосты — не слова
    if not words:
        return ""
    if all(w in _MARK_STOP_EXACT or any(w.startswith(s) for s in _MARK_STOP_STEM) for w in words):
        return ""
    return city


def parse_title(title):
    """(город, зона, время приезда, самая ранняя заявка, московский ли прайс)."""
    t = (title or "").strip()
    msk = bool(_MSK_RX.search(t))
    if _SOGLAS_RX.search(t):
        zone, lead, early = "outside", "два, два с половиной часа", "11:00"
    elif _H3_RX.search(t):
        zone, lead, early = "satellite", "три часа", "11:00"
    elif _H25_RX.search(t):
        zone, lead, early = "satellite", "два, два с половиной часа", "11:00"
    else:
        zone, lead, early = "city", "час-полтора", "10:00"
        if _H15_RX.search(t):
            lead = "час-полтора"
    # имя города: снимаем служебные хвосты в скобках и пометки времени
    city = re.sub(r"\([^)]*\)", " ", t)
    # «ЗА 1,5 ЧАСА», «ЗА 2-2.5», «за 3 часа» — запятая внутри числа, поэтому [^,]* не годится
    # ⚠ ГРАНИЦА СЛОВА С ОБЕИХ СТОРОН: без второго \b предлог «за» срезался ВНУТРИ слова, и
    # «ПРИЁМ ЗАЯВОК В ТЕЧЕНИИ 2-2,5 ЧАСОВ» превращался в «Приём Явок В Течении» — метка
    # считалась названием города и не применялась к своему контуру.
    city = re.sub(r"(?i)\bза\b\s*[\d.,\s\-–]*(?:час\w*)?", " ", city)
    city = re.sub(r"(?i)\b(?:принимаем.*|по\s+соглас\w*.*)", " ", city)
    city = re.sub(r"[,;].*$", "", city)
    city = re.sub(r"\s{2,}", " ", city).strip(" -–—_")
    city = re.sub(r"\s+\d+$", "", city)          # «МОСКВА 1» → «МОСКВА»
    return (city.title() if city else ""), zone, lead, early, msk


#: Ниже этого числа выгрузка считается неудавшейся. Сегодня на трёх картах 78 контуров;
#: порог с запасом вниз, чтобы честное удаление пары зон не блокировало обновление, а
#: пустой или обрезанный ответ Яндекса — блокировал.
_МИНИМУМ_КОНТУРОВ = 40

#: Когда каждая карта правилась в конструкторе — заполняет `collect`, читает `main`.
_ПРАВЛЕНО = {}


def _прежние():
    """Прошлая выгрузка — чтобы сравнить и не принять заведомо худшую."""
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _линтер(areas):
    """Сверка имён с городами справочника. Ругаемся, но выгрузку не блокируем.

    ⚠ КАРТА И СПРАВОЧНИК ГОВОРЯТ НА РАЗНЫХ ЯЗЫКАХ, И ЭТО СТОИЛО ПЕТЕРБУРГА. Контур подписан
    «СПБ 1», город из него выходит «Спб», а справочник знает «Санкт-Петербург» — и семь
    контуров Петербурга на трёх картах до бота не доходили. Разрыв был не виден ничем:
    выгрузка молчала, бот молчал, узнать можно было только прогоном руками.
    """
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "brain"))
        import filials as _f
    except Exception:
        return
    чужие = []
    for a in areas:
        имя = (a.get("name") or "").strip()
        гор = (a.get("city") or "").strip()
        # ⚠ КОНТУР, ПОДПИСАННЫЙ ПРАВИЛОМ, ГОРОДА И НЕ ДОЛЖЕН ИМЕТЬ. «ПО СОГЛАСОВАНИЮ»,
        # «Принимаем за 2-2.5 часа» — это условие приёма для области, а не название города.
        # Ругаться на них значит приучить читателя пролистывать вывод линтера.
        if not гор and re.search(r"(?i)соглас|принима|за\s*\d", имя):
            continue
        if not гор:
            чужие.append((a["direction"], имя or "(без названия)", "имени города нет"))
            continue
        # ⚠ СИНОНИМ — ЭТО РЕШЁННЫЙ СЛУЧАЙ. «Спб» боту доступен через `имена_города`, и линтер,
        # ругаясь на него, кричал бы о том, что уже починено.
        имена = _f.имена_города(гор) | {_f._norm(гор)}
        if any(_f.known(и, a["direction"]) or _f.known(и) for и in имена):
            continue
        чужие.append((a["direction"], имя, "«%s» нет в справочнике" % гор))
    if чужие:
        print("\n!!! ЛИНТЕР ИМЁН: контуров, чей город не опознан, %d" % len(чужие))
        for d, имя, почему in чужие:
            print("    %-4s %-44s %s" % (d, имя[:44], почему))
        print("    Такой контур боту недоступен: он ищет город по имени. Либо переименуйте")
        print("    объект на карте, либо добавьте синоним в brain/filials.py (СИНОНИМЫ_ГОРОДОВ).")


def main():
    dump = "--dump" in sys.argv
    areas, lines = [], []
    for direction, h in MAPS.items():
        polys, _lines = collect(direction, h, dump=dump)
        for p in polys:
            city, zone, lead, early, msk = parse_title(p["title"])
            areas.append({"direction": direction, "name": p["title"], "city": city, "zone": zone,
                          "lead": lead, "earliest": early, "msk": msk, "ring": p["ring"]})
        # ⚠ ЛИНИИ СОХРАНЯЕМ ТОЖЕ. Раньше выгрузка их молча выбрасывала, а заказчик рисует ими
        # служебные границы («Граница Красногорск», «Граница ГАТЧИНА-СПБ4», а у МНЧ-Одинцово
        # прямо в названии «левая сторона 2-2,5 ч. | правая 1-1,5 ч.»). Право приёма определяет
        # ПОЛИГОН — часть города внутри контура принимаем сами, часть снаружи идёт по
        # согласованию, — но разметку заказчика надо хранить целиком, ничего не теряя.
        for ln in _lines:
            if ln.get("title") or ln.get("ring"):
                lines.append({"direction": direction, "name": ln["title"], "ring": ln["ring"]})
    if dump:
        return 0
    # МЕТКИ со страницы карт: без них время приёма у 20 контуров было неверным (см. apply_marks)
    marks_by_dir, all_marks = {}, []
    for direction, h in MAPS.items():
        try:
            _html = fetch_page(h)
            mk = marks(_html)
            # линии, которые виджет отдал пустыми, добираем со страницы
            _have = {(l["name"], len(l["ring"])) for l in lines if l["direction"] == direction}
            _names = {l["name"] for l in lines if l["direction"] == direction and l["ring"]}
            for pl in page_lines(_html):
                if pl["title"] not in _names:
                    lines.append({"direction": direction, "name": pl["title"], "ring": pl["ring"]})
                    _names.add(pl["title"])
        except Exception as e:                       # noqa: BLE001 — сеть не должна ронять выгрузку
            print("   ! метки %s не забраны: %s" % (direction, str(e)[:80]))
            mk = []
        marks_by_dir[direction] = mk
        all_marks += [{"direction": direction, "name": t, "lat": la, "lon": lo} for t, la, lo in mk]
    changed, city_rules = apply_marks(areas, marks_by_dir)

    # ⚠⚠ ВЫГРУЗКА ОБЯЗАНА ПАДАТЬ ГРОМКО, А НЕ ТИХО. Здесь не было НИ ОДНОЙ проверки: сменит
    # Яндекс формат виджета или отдаст пустую страницу — файл перезапишется пустым, и бот
    # останется без территорий вовсе, ничего никому не сказав. Территория решает, берём ли мы
    # заявку и когда приедем, поэтому «пусто» — худший из возможных исходов, хуже старых данных.
    беда = []
    if len(areas) < _МИНИМУМ_КОНТУРОВ:
        беда.append("контуров %d, ожидалось хотя бы %d" % (len(areas), _МИНИМУМ_КОНТУРОВ))
    пусто = [d for d in MAPS if not any(a["direction"] == d for a in areas)]
    if пусто:
        беда.append("направления без единого контура: %s" % ", ".join(пусто))
    было_ = _прежние()
    if было_:
        for d in MAPS:
            стало_n = sum(1 for a in areas if a["direction"] == d)
            было_n = sum(1 for a in было_.get("areas", []) if a.get("direction") == d)
            # ⚠ УМЕНЬШЕНИЕ — ПОВОД ОСТАНОВИТЬСЯ, А НЕ ПРОДОЛЖИТЬ. Владелец контуры добавляет,
            # а не стирает; резкая убыль почти наверняка значит, что карта не догрузилась.
            if было_n and стало_n < было_n * 0.8:
                беда.append("%s: контуров было %d, стало %d" % (d, было_n, стало_n))
    if беда:
        print("\n!!! ВЫГРУЗКА НЕ ПРИНЯТА, файл НЕ тронут:")
        for б in беда:
            print("    · " + б)
        print("    Старые территории остаются в силе — это лучше, чем никакие.")
        return 2

    _линтер(areas)

    # ⚠ ЗАПИСЬ АТОМАРНАЯ. Прерванная запись оставляла битый JSON, а `territory.zones()` глотает
    # ошибку разбора и возвращает пустоту — то есть одна неудачная выгрузка молча выключала
    # карту целиком. Пишем во временный файл рядом и подменяем одним движением.
    итог = {"areas": areas, "lines": lines, "marks": all_marks, "city_rules": city_rules,
            # ⚠ ПО ФАЙЛУ ДОЛЖНО БЫТЬ ВИДНО, КОГДА ОН СНЯТ И С ЧЕГО. Без этого понять, устарел
            # ли он, нельзя ничем, кроме повторной выгрузки — а карты владелец правит постоянно.
            "снято": time.strftime("%Y-%m-%d %H:%M:%S"),
            "карты": {d: h[:12] for d, h in MAPS.items()},
            "правлено_на_картах": dict(_ПРАВЛЕНО)}
    врем = OUT + ".tmp"
    with open(врем, "w", encoding="utf-8") as f:
        json.dump(итог, f, ensure_ascii=False)
    json.load(open(врем, encoding="utf-8"))          # читается — значит записалось целиком
    os.replace(врем, OUT)
    print("записано: %s (%d контуров, %d линий, %d меток)"
          % (OUT, len(areas), len(lines), len(all_marks)))
    if city_rules:
        print("\n=== ПРАВИЛА ГОРОДОВ ИЗ МЕТОК (%d) ===" % len(city_rules))
        for r in city_rules:
            print("   %-4s %-20s %-26s (метка «%s»)" % (r["direction"], r["city"][:20], r["lead"], r["mark"][:34]))
    if changed:
        print("\n=== ВРЕМЯ ПРИЁМА ИСПРАВЛЕНО ПО МЕТКАМ (%d контуров) ===" % len(changed))
        for d, name, was, now, mark in changed:
            print("   %-4s %-30s %-22s → %-26s (метка «%s»)"
                  % (d, name[:30], was, now, mark[:34]))
    if lines:
        print("\n=== ЛИНИИ (служебная разметка заказчика)")
        for ln in lines:
            print("   %-4s %-52s точек %d" % (ln["direction"], (ln["name"] or "(без названия)")[:52],
                                              len(ln["ring"])))
    by_dir = {}
    for a in areas:
        by_dir.setdefault(a["direction"], []).append(a)
    for d, lst in sorted(by_dir.items()):
        print("\n=== %s: %d контуров" % (d.upper(), len(lst)))
        for a in sorted(lst, key=lambda x: (x["zone"], x["city"])):
            print("   %-9s %-26s %-30s %s%s" % (a["zone"], a["city"][:26], a["lead"][:30],
                                                "МСК " if a["msk"] else "", "точек %d" % len(a["ring"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
