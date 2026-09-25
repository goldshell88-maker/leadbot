# -*- coding: utf-8 -*-
"""АДРЕС → КООРДИНАТЫ (геокодер Яндекса), чтобы бот точно понимал, где клиент.

Зачем: определять зону филиала по НАЗВАНИЮ города — грубо. Клиент пишет «Пирогово, дом 3»
или «Ленина 5», и попадает он или нет в границы филиала, видно только по точке на карте.
Полигоны лежат в brain/filial_zones.json (см. territory.py), а сюда приходит адрес.

Ключ берётся из (в порядке приоритета):
  1) переменная окружения LEADBOT_YANDEX_KEY
  2) ключ "yandex_geocoder_key" в config.json
Ключа нет → geocode() возвращает None, и бот спокойно работает по названиям городов, как раньше.

Кэш на диске (data/geocode_cache.json): один и тот же адрес не геокодируем дважды —
и быстрее, и лимит запросов не жжём.
"""
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

import paths

CACHE_FILE = os.path.join(paths.ROOT, "data", "geocode_cache.json")
_LOCK = threading.Lock()
_CACHE = None
API = "https://geocode-maps.yandex.ru/1.x/"

# ЗАПАСНОЙ ГЕОКОДЕР — OpenStreetMap/Nominatim, без ключей.
# Зачем: ключа Яндекса нет с сессии 39c, и из-за этого ВСЯ карта филиалов (77 контуров,
# 20 016 точек) простаивала — locate_by_text молча возвращал None, территория определялась
# только по тексту адреса. OSM даёт координаты бесплатно и включает карты уже сейчас.
# ⚠ У Nominatim жёсткие правила: не чаще 1 запроса в секунду и осмысленный User-Agent.
# Поэтому здесь стоит ограничитель частоты, а результат кэшируется на диске навсегда.
# Ключ Яндекса всё равно лучше (точнее на российских адресах) — как появится, он имеет приоритет.
OSM_API = "https://nominatim.openstreetmap.org/search"
OSM_UA = "leadbot/1.0 (dispatch assistant; contact via project owner)"
_OSM_MIN_INTERVAL = 1.1
_osm_last = [0.0]
# ⚠ КОРОТКАЯ ПАУЗА ПОСЛЕ СБОЯ. Ошибку сервиса в постоянный кэш класть нельзя (адрес «залипнет»
# ненайденным навсегда), но и ходить в сеть КАЖДЫЙ ход тоже нельзя: при 429 каждый ответ бота
# получал бы лишние секунды ожидания на ровном месте. Держим отказ в памяти процесса 10 минут.
_FAIL_TTL = 600
_osm_fail = {}


def _key():
    env = os.environ.get("LEADBOT_YANDEX_KEY")
    if env:
        return env.strip()
    try:
        with open(paths.CONFIG_FILE, "r", encoding="utf-8") as f:
            return ((json.load(f) or {}).get("yandex_geocoder_key") or "").strip()
    except Exception:
        return ""


def _osm_allowed():
    """Запасной геокодер можно выключить: "geocoder": "yandex-only" в config.json."""
    try:
        with open(paths.CONFIG_FILE, "r", encoding="utf-8") as f:
            return ((json.load(f) or {}).get("geocoder") or "").strip().lower() != "yandex-only"
    except Exception:
        return True


def provider():
    if _key():
        return "yandex"
    return "osm" if _osm_allowed() else ""


def enabled():
    return bool(provider())


ERROR = object()          # «сервис не ответил» — это НЕ «адрес не найден», кэшировать нельзя


def _osm_geocode(q, timeout):
    """Nominatim. (широта, долгота) | None (адреса нет) | ERROR (сервис не ответил).

    ⚠ Различать обязательно. Nominatim легко отдаёт HTTP 429 «Too many requests» (поймано
    01.08 на живой пробе), и если такой ответ записать в кэш как «адреса нет», то адрес
    останется ненайденным НАВСЕГДА — территория по нему больше никогда не определится."""
    with _LOCK:
        wait = _OSM_MIN_INTERVAL - (time.time() - _osm_last[0])
        if wait > 0:
            time.sleep(min(wait, _OSM_MIN_INTERVAL))
        _osm_last[0] = time.time()
    try:
        url = OSM_API + "?" + urllib.parse.urlencode(
            {"q": q, "format": "json", "limit": 1, "accept-language": "ru",
             "countrycodes": "ru", "addressdetails": 1})
        req = urllib.request.Request(url, headers={"User-Agent": OSM_UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            arr = json.loads(r.read().decode("utf-8", "replace"))
        if not arr:
            return None
        # ⚠ СОСТАВ АДРЕСА БЕРЁМ ТЕМ ЖЕ ЗАПРОСОМ. Ключа Яндекса нет ни локально, ни в бою —
        # работает запасной путь OSM, и разбор состава обязан жить и здесь, иначе название
        # населённого пункта («посёлок Дубовое») не появится никогда.
        _запомнить_состав_osm(q, arr[0])
        return (float(arr[0]["lat"]), float(arr[0]["lon"]))
    except Exception:
        return ERROR


def _cache():
    global _CACHE
    if _CACHE is None:
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                _CACHE = json.load(f) or {}
        except Exception:
            _CACHE = {}
    return _CACHE


def _save_cache():
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_CACHE, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass


def _norm_query(city, address):
    parts = [p.strip(" ,;") for p in (city or "", address or "") if (p or "").strip()]
    q = ", ".join(parts)
    q = re.sub(r"\s{2,}", " ", q).strip(" ,")
    return q


#: Состав адреса по данным карты: {запрос: {"locality": "посёлок Дубовое", "street": "Зелёная улица"}}.
#: Держим в памяти процесса, а не в файле кэша координат: у того формат [широта, долгота] и он
#: старше — смешав два формата в одном файле, однажды прочитаем список как словарь.
_СОСТАВ = {}


def _запомнить_состав(запрос, member):
    """Вытащить из ответа Яндекса название пункта и улицы и положить рядом с координатами."""
    try:
        мета = ((member.get("GeoObject") or {}).get("metaDataProperty") or {})
        компоненты = (((мета.get("GeocoderMetaData") or {}).get("Address") or {})
                      .get("Components") or [])
        состав = {}
        for c in компоненты:
            вид = (c.get("kind") or "").lower()
            имя = (c.get("name") or "").strip()
            if вид == "locality" and имя:
                состав["locality"] = имя
            elif вид == "street" and имя:
                состав["street"] = имя
        if состав:
            with _LOCK:
                _СОСТАВ[запрос] = состав
    except Exception:
        pass                       # состав — приятное дополнение, ронять из-за него ход нельзя


#: Как OSM называет тип пункта → как его пишет карточка CRM. «village» это «посёлок»,
#: и владелец ждёт в поле именно «посёлок Дубовое», а не голое «Дубовое».
_ТИП_ПУНКТА = {"village": "посёлок", "hamlet": "посёлок", "town": "город", "city": "город",
               "suburb": "микрорайон", "municipality": "посёлок"}


def _запомнить_состав_osm(запрос, объект):
    """Пункт и улица из ответа Nominatim (`addressdetails=1`)."""
    try:
        адрес = объект.get("address") or {}
        состав = {}
        for ключ in ("village", "hamlet", "town", "city", "municipality"):
            имя = (адрес.get(ключ) or "").strip()
            if имя:
                тип = _ТИП_ПУНКТА.get(ключ, "")
                состав["locality"] = (тип + " " + имя).strip() if тип and тип != "город" else имя
                состав["locality_kind"] = ключ
                break
        улица = (адрес.get("road") or "").strip()
        if улица:
            состав["street"] = улица
        # ⚠ НОМЕР ДОМА ТОЖЕ ЗАПОМИНАЕМ (28.08, замечание владельца по заявке №1234567).
        # «Тверской 10» в Москве по факту нет — есть «10с1», и карта это знает: на запрос
        # с домом она возвращает house_number, а когда дома нет — привязывает к улице и
        # house_number не отдаёт вовсе. Разница и есть ответ на вопрос «такой адрес
        # существует?». Раньше номер дома из ответа выбрасывался, и спросить было нечем.
        дом = (адрес.get("house_number") or "").strip()
        if состав:
            # ⚠ КЛЮЧ СТАВИМ ДАЖЕ ПУСТЫМ. Его ОТСУТСТВИЕ означает «эту запись собрали до
            # правки и про дом карту не спрашивали», а пустое значение — «спрашивали,
            # дома нет». Без этого различия 5879 старых записей кэша молча отвечали бы
            # «дома не существует» на любой адрес.
            состав["house"] = дом
            # ⚠ ЧТО ИМЕННО СТОИТ ПО АДРЕСУ (28.08). На «Тверская 10» карта отвечает
            # type=construction — там СТРОЙКА, а не жилой дом, и владелец говорит про
            # этот адрес «такого нет, есть 10с1». Номер сам по себе этого не показывает:
            # house='10' выглядит как подтверждение. Тип объекта показывает.
            состав["kind"] = (объект.get("type") or "").strip().lower()
            with _LOCK:
                _СОСТАВ[запрос] = состав
    except Exception:
        pass


#: типы улиц снимаем перед сравнением: «улица Ленина» и «Ленина» — одно и то же
_ТИП_УЛИЦЫ_RX = re.compile(
    r"\b(улиц[аыуе]|ул\.?|проспект[а-я]*|просп?\.?|пр-?кт|переул[а-я]*|пер\.?|шоссе|"
    r"бульвар[а-я]*|б-?р|набережн[а-я]+|наб\.?|проезд[а-я]*|площад[ьи]|пл\.?|"
    r"тупик[а-я]*|аллея|линия|линии)\b", re.IGNORECASE)


def _без_города(улица, city):
    """Название города внутри строки улицы мешает сравнению.

    ⚠ ЗАМЕР НА 32 ЖИВЫХ АДРЕСАХ (28.08): клиенты пишут «белгород улица Садовая 12»
    и «Абакан проезд лесной 5» — город прямо в строке. Карта возвращает улицу без
    него, множества слов не совпадали, и своя же улица объявлялась чужой.
    """
    у = (улица or "")
    г = (city or "").strip()
    if г:
        у = re.sub(r"\b" + re.escape(г) + r"\b", " ", у, flags=re.IGNORECASE)
        корень = re.sub(r"[аяое]$", "", г.lower())
        if len(корень) > 4:
            у = re.sub(r"\b" + re.escape(корень) + r"[а-яё]{0,3}\b", " ", у, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", у).strip(" ,.")


def улицы_совпали(своя, с_карты):
    """Одна ли это улица. Разные названия с общим словом — НЕ одно и то же.

    ⚠ ПОЙМАНО НА ЖИВОЙ ПРОБЕ (28.08). На запрос «Тверская 27» карта вернула дом 27
    на «4-й Тверской-Ямской улице» — это ДРУГАЯ улица в другом месте. Подтвердить по
    такому ответу значит подтвердить адрес, которого клиент не называл, и отправить
    мастера не туда: ровно та беда, ради которой в проекте уже стоит граница слова.

    Сравниваем РАВЕНСТВО множеств значимых слов, а не вложенность. Вложенность
    сначала казалась мягче и добрее, но на пробе склеила «Садовую» с «Большой
    Садовой» — а это разные улицы, и «Большая» здесь не уточнение, а различитель.
    Дефисное слово не разбиваем: иначе половина чужого «Тверская-Ямская» совпадёт
    с целым нашим «Тверская».

    Отбрасываем токены С ТОЧКОЙ («В.О.», «П.С.») — это пометка берега, а не имя
    улицы: «Малый проспект В.О.» и «Малый проспект» карта считает одним объектом.
    """
    def слова(x, снимать_тип=True):
        x = (x or "").lower().replace("ё", "е")
        if снимать_тип:
            x = _ТИП_УЛИЦЫ_RX.sub(" ", x)
        return {w for w in re.split(r"[\s,]+", x)
                if w and "." not in w and w not in ("им", "имени")}
    a, b = слова(своя), слова(с_карты)
    # ⚠ НАЗВАНИЕ ИЗ ОДНОГО ТИПА — «НАБЕРЕЖНАЯ УЛИЦА», «ПРОСПЕКТ МИРА» БЕЗ «МИРА».
    # Поймано замером на 32 живых адресах: у «Набережной улицы» после снятия типа не
    # оставалось НИ ОДНОГО слова с обеих сторон, и улица объявлялась чужой сама себе.
    if not a or not b:
        a, b = слова(своя, False), слова(с_карты, False)
    if not a or not b:
        return False
    return a == b


#: объекты, которые НЕ являются готовым жилым адресом
_НЕ_ДОМ = {"construction", "proposed", "demolished", "ruins"}
_ГРЕЕМ = set()


def прогреть(address, city=""):
    """Спросить карту в фоне, чтобы к СЛЕДУЮЩЕМУ ходу ответ лежал в кэше.

    ⚠ НА ПУТИ ОТВЕТА КЛИЕНТУ СЕТИ БЫТЬ НЕ ДОЛЖНО: бюджет хода 9 секунд, и скорость
    ответа — сильнейший приём бота из всех измеренных. Поэтому адрес греем сразу, как
    только он прозвучал, а спрашиваем клиента уже по готовому. Один адрес — один поход.
    """
    try:
        q = _norm_query(city, address)
        if len(q) < 5 or len(q) > 160:
            return
        with _LOCK:
            if q in _ГРЕЕМ or q in _СОСТАВ:
                return
            _ГРЕЕМ.add(q)
        threading.Thread(target=lambda: parts(address, city, timeout=8), daemon=True).start()
    except Exception:
        pass


#: ⚠ ТОЛЬКО ЭТИ ДВА ЗЕРКАЛА ОТДАЮТ ДАННЫЕ ПО РОССИИ (замер 29.08). Проверять надо
#: НЕПУСТОЙ ответ, а не HTTP 200: overpass.osm.ch отвечал 3 раза из 3 и все три раза
#: пустотой — у него только швейцарская база. Тот же класс ошибки, что заглушка
#: «Выбрать ...» в справочнике CRM: проверяли доступность вместо пригодности.
#: Каждое зеркало по отдельности даёт 2 попадания из 3; перебор двух — около 89 %.
OVERPASS = ("https://overpass-api.de/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter")
_ОРИЕНТИРЫ = {}
_ГРЕЕМ_ОР = set()
#: что годится в ориентир и как об этом говорят. Порядок — приоритет: сетевой магазин
#: узнаваем всем, аптека почти всем, школа — привязка «напротив».
_ВИД_ОРИЕНТИРА = (("supermarket", "магазин"), ("convenience", "магазин"),
                  ("chemist", "магазин"), ("pharmacy", "аптека"),
                  ("school", "школа"), ("kindergarten", "детский сад"))


def _overpass(lat, lon, радиус=150, timeout=12):
    q = ('[out:json][timeout:12];'
         '(node(around:%d,%f,%f)["shop"~"supermarket|convenience|chemist"];'
         ' node(around:%d,%f,%f)["amenity"~"pharmacy|school|kindergarten"];);out body 30;'
         % (радиус, lat, lon, радиус, lat, lon))
    for url in OVERPASS:
        try:
            req = urllib.request.Request(
                url, data=urllib.parse.urlencode({"data": q}).encode(),
                headers={"User-Agent": OSM_UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                эл = (json.loads(r.read().decode("utf-8", "replace")) or {}).get("elements")
            if эл:
                return эл
        except Exception:
            continue
    return None


def _лучший_ориентир(элементы):
    """Самый узнаваемый объект: сетевой магазин → аптека → школа. Без имени не берём."""
    порядок = {к: i for i, (к, _) in enumerate(_ВИД_ОРИЕНТИРА)}
    лучший = None
    for e in (элементы or []):
        t = e.get("tags") or {}
        вид = t.get("shop") or t.get("amenity") or ""
        if вид not in порядок:
            continue
        имя = (t.get("brand") or t.get("name") or "").strip()
        # ⚠ БЕЗ ИМЕНИ ОРИЕНТИР БЕСПОЛЕЗЕН для магазина: «у вас рядом магазин, верно?»
        # звучит как угадывание. Школе и аптеке имя не нужно — их и так узнают.
        if not имя and вид in ("supermarket", "convenience", "chemist"):
            continue
        ключ = (порядок[вид], 0 if t.get("brand") else 1)
        if лучший is None or ключ < лучший[0]:
            лучший = (ключ, {"имя": имя, "вид": dict(_ВИД_ОРИЕНТИРА)[вид]})
    return лучший[1] if лучший else {}


def прогреть_ориентир(address, city=""):
    """Спросить про соседей дома в фоне — к следующему ходу ответ будет в памяти."""
    try:
        q = _norm_query(city, address)
        with _LOCK:
            if q in _ГРЕЕМ_ОР or q in _ОРИЕНТИРЫ:
                return
            _ГРЕЕМ_ОР.add(q)

        def работа():
            точка = geocode(address, city, timeout=8)
            найдено = {}
            if точка and точка is not ERROR:
                найдено = _лучший_ориентир(_overpass(точка[0], точка[1]))
            with _LOCK:
                _ОРИЕНТИРЫ[q] = найдено      # пусто тоже запоминаем: второй раз не ходим

        threading.Thread(target=работа, daemon=True).start()
    except Exception:
        pass


def ориентир(address, city=""):
    """{"имя": "Пятёрочка", "вид": "магазин"} — строго из памяти, без похода в сеть."""
    with _LOCK:
        return dict(_ОРИЕНТИРЫ.get(_norm_query(city, address)) or {})


def сомнение(address, city=""):
    """Чем адрес подозрителен — СТРОГО из кэша, без единого сетевого вызова.

    Возвращает ("", "") когда сомнений нет, иначе (вид_сомнения, номер_из_карты).
    Виды: "стройка" — по адресу стройка, а не дом; "строение" — карта знает номер
    с уточнением («27 с1»), а клиент назвал голый; "нет_дома" — улица есть, дома нет;
    "чужая_улица" — карта нашла другую улицу.
    """
    try:
        состав = parts_cached(address, city) or {}
        if "house" not in состав:
            return "", ""                      # карту ещё не спрашивали — молчим
        улица_адреса = _без_города(re.sub(r"[\d].*$", "", (address or "")), city)
        если_улица = (состав.get("street") or "").strip()
        if если_улица and not улицы_совпали(улица_адреса, если_улица):
            return "чужая_улица", если_улица
        дом_карты = (состав.get("house") or "").strip()
        if not дом_карты:
            return "нет_дома", ""
        if (состав.get("kind") or "") in _НЕ_ДОМ:
            return "стройка", дом_карты
        # карта знает уточнение, а клиент назвал голый номер: «27» против «27 с1»
        свой = re.sub(r"^\D*", "", (address or "")).strip()
        if свой and _голый_номер(свой) and not _голый_номер(дом_карты) \
                and _цифры_дома(дом_карты) == _цифры_дома(свой):
            return "строение", дом_карты
        return "", ""
    except Exception:
        return "", ""


def _голый_номер(x):
    """«10» — да; «10с1», «137/2», «10 к2» — нет."""
    return bool(re.fullmatch(r"\s*\d+\s*", x or ""))


def _цифры_дома(x):
    m = re.match(r"\s*(\d+)", x or "")
    return m.group(1) if m else ""


def дом_подтверждён(address, city="", timeout=6):
    """Знает ли карта такой ДОМ. Возвращает словарь, никогда не бросает.

    {"есть_дом": bool, "дом": "27 с1", "улица": "Тверская улица", "карта": bool}

    ⚠ ЭТО ПОДСКАЗКА, А НЕ ЗАПРЕТ. Карта неполна: новостройки и частный сектор в OSM
    появляются с опозданием, и отказывать клиенту из-за молчания карты нельзя. Поэтому
    ответ идёт предупреждением диспетчеру, а заявка создаётся как обычно.
    `карта: False` значит «сервис не ответил» — это не то же самое, что «дома нет».
    """
    out = {"есть_дом": False, "дом": "", "улица": "", "карта": False,
           "улица_чужая": False, "стройка": False}
    try:
        состав = parts_cached(address, city) or {}
        if "house" not in состав:
            # запись старая (или её нет) — идём к карте, как это делает parts()
            состав = parts(address, city, timeout=timeout) or {}
        if "house" not in состав:
            return out                             # карта не ответила: молчание ≠ «нет дома»
        out["карта"] = True
        out["дом"] = (состав.get("house") or "").strip()
        out["улица"] = (состав.get("street") or "").strip()
        # улица из ответа чужая — значит карта нашла НЕ ТО, и её «дом» ничего не значит
        своя_улица = _без_города(re.sub(r"[\d].*$", "", (address or "")), city)
        out["улица_чужая"] = bool(out["улица"]) and not улицы_совпали(
            своя_улица, out["улица"])
        out["есть_дом"] = bool(out["дом"]) and not out["улица_чужая"]
        # стройка — единственный случай «дома нет», который стоит показывать человеку
        out["стройка"] = (состав.get("kind") or "") in _НЕ_ДОМ
    except Exception:
        pass
    return out


def parts_cached(address, city=""):
    """Состав адреса ТОЛЬКО из памяти процесса, без единого сетевого вызова.

    ⚠ ЗАЧЕМ ОТДЕЛЬНАЯ ДВЕРЬ. На пути ОТВЕТА клиенту бюджет хода 9 секунд, и блокирующий поход
    в карту там недопустим: медленный сервис съест ход целиком (ровно так мы уже теряли ходы на
    скачивании снимков). Заявка же считается ПОСЛЕ ответа — там `parts` ходит в сеть и кладёт
    состав сюда. Значит на ответе мы пользуемся тем, что карта сказала на прошлом ходу, и ничем
    не рискуем: не знаем — ведём себя как раньше.
    """
    with _LOCK:
        return dict(_СОСТАВ.get(_norm_query(city, address)) or {})


def parts(address, city="", timeout=6):
    """Состав адреса по карте: {"locality": …, "street": …}. Пусто — карта не ответила.

    Координаты и состав берутся ОДНИМ запросом: `geocode` по пути запоминает состав, поэтому
    здесь мы либо читаем готовое, либо зовём `geocode` и читаем после него.
    """
    q = _norm_query(city, address)
    with _LOCK:
        готовое = _СОСТАВ.get(q)
    if готовое:
        return dict(готовое)
    # ⚠ КЭШ КООРДИНАТ ЗАКРЫВАЕТ ДОРОГУ К СОСТАВУ, И ЭТО НЕ МЕЛОЧЬ. `geocode` при попадании в
    # кэш возвращается, не сходив в сервис, — а состав в кэше не лежит (5879 записей собраны
    # до этой правки). Поэтому здесь ходим за составом САМИ, мимо кэша координат: он про
    # координаты и остаётся про них.
    if len(q) < 5 or len(q) > 160:
        return {}
    if not _key() and not _osm_allowed():
        return {}
    try:
        if _key():
            geocode(address, city, timeout=timeout)          # яндекс-путь пишет состав по дороге
        else:
            res = _osm_geocode(q, max(timeout, 8))
            if res is ERROR:
                return {}
    except Exception:
        return {}
    with _LOCK:
        return dict(_СОСТАВ.get(q) or {})


def geocode(address, city="", timeout=6):
    """(широта, долгота) либо None. Никогда не бросает исключений — гео не должно ронять ответ."""
    q = _norm_query(city, address)
    if len(q) < 5:
        return None
    # ⚠ ПРЕДОХРАНИТЕЛЬ ОТ ЦЕЛЫХ ПЕРЕПИСОК. Длинная строка — это не адрес: геокодер по ней ничего
    # не найдёт, во ВНЕШНИЙ сервис уйдёт речь клиента целиком, а кэш забьётся мусором (01.08 так
    # набралось 11 091 запись и 5.6 МБ, ключи до 3152 символов). Вызывающий код обязан подавать
    # адресную часть (server._geo_text), но защита должна стоять и здесь.
    if len(q) > 160:
        return None
    key = _key()
    if not key and not _osm_allowed():
        return None
    with _LOCK:
        c = _cache()
        if q in c:
            v = c[q]
            return tuple(v) if v else None
    if not key:                                   # запасной путь: OpenStreetMap, без ключей
        with _LOCK:
            until = _osm_fail.get(q, 0)
        if until > time.time():
            return None                           # недавно сбоил — не тратим ход на ожидание
        res = _osm_geocode(q, max(timeout, 8))
        if res is ERROR:
            with _LOCK:
                _osm_fail[q] = time.time() + _FAIL_TTL
                if len(_osm_fail) > 500:          # память процесса не растим бесконечно
                    _now = time.time()
                    for k in [k for k, v in _osm_fail.items() if v < _now]:
                        _osm_fail.pop(k, None)
            return None                           # молча деградируем, но кэш НЕ портим
        with _LOCK:
            _cache()[q] = list(res) if res else None
            _save_cache()
        return res
    try:
        url = API + "?" + urllib.parse.urlencode({
            "apikey": key, "geocode": q, "format": "json", "results": 1, "lang": "ru_RU"})
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        members = (((data.get("response") or {}).get("GeoObjectCollection") or {})
                   .get("featureMember") or [])
        if not members:
            res = None
        else:
            pos = (((members[0].get("GeoObject") or {}).get("Point") or {}).get("pos") or "")
            lon, lat = [float(x) for x in pos.split()]   # Яндекс отдаёт «долгота широта»
            res = (lat, lon)
            # ⚠ НАЗВАНИЕ НАСЕЛЁННОГО ПУНКТА КАРТА ЗНАЕТ, А МЫ ЕГО ВЫБРАСЫВАЛИ. Клиент пишет
            # «Дубовое, центр..», а по картам это «посёлок Дубовое» — и именно так его ждёт
            # карточка CRM (правка владельца 27.08). Разбор адреса по словам опознаёт пункт
            # только со словом-типом («посёлок», «село»), поэтому голое имя терялось, а вместе
            # с ним и признак «ехать за черту города».
            _запомнить_состав(q, members[0])
    except Exception:
        return None                                       # сеть/лимит/ключ — молча деградируем
    with _LOCK:
        _cache()[q] = list(res) if res else None
        _save_cache()
    return res
