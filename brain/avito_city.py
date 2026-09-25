# -*- coding: utf-8 -*-
"""Город из ссылки Avito (page.url в карточке Jivo).

Avito кладёт город в первый сегмент пути: avito.ru/<город>/... . Для городов, чьё
имя совпадает в разных регионах, встречается префикс региона
(amurskaya_oblast_blagoveschensk) или города-миллионника (moskva_zelenograd,
sankt-peterburg_kolpino). Словарь покрывает все города, встреченные в данных;
для незнакомых — аккуратный фолбэк по последнему сегменту.
"""
import re

CITY_BY_SLUG = {
    "abakan": "Абакан", "achinsk": "Ачинск",
    "amurskaya_oblast_blagoveschensk": "Благовещенск", "anapa": "Анапа",
    "angarsk": "Ангарск", "aprelevka": "Апрелевка", "armavir": "Армавир",
    "astrahan": "Астрахань", "balashiha": "Балашиха", "barnaul": "Барнаул",
    "belgorod": "Белгород", "berdsk": "Бердск", "berezniki": "Березники",
    "biysk": "Бийск", "bryansk": "Брянск", "chelyabinsk": "Челябинск",
    "cherepovets": "Череповец", "chita": "Чита", "dmitrov": "Дмитров",
    "dolgoprudnyy": "Долгопрудный", "domodedovo": "Домодедово",
    "ekaterinburg": "Екатеринбург", "elektrostal": "Электросталь", "elets": "Елец",
    "engels": "Энгельс", "feodosiya": "Феодосия", "gatchina": "Гатчина",
    "gelendzhik": "Геленджик", "gubkin": "Губкин", "habarovsk": "Хабаровск",
    "himki": "Химки", "irkutsk": "Иркутск", "ivanovo": "Иваново",
    "izhevsk": "Ижевск", "kaliningrad": "Калининград", "kaluga": "Калуга",
    "kazan": "Казань", "kemerovo": "Кемерово", "kerch": "Керчь",
    "kirovskaya_oblast_kirov": "Киров", "kislovodsk": "Кисловодск", "klin": "Клин",
    "kommunarka": "Коммунарка", "korolev": "Королёв", "kostroma": "Кострома",
    "kovrov": "Ковров", "krasnodar": "Краснодар", "krasnokamsk": "Краснокамск",
    "krasnoyarsk": "Красноярск", "krasnoyarskiy_kray_sosnovoborsk": "Сосновоборск",
    "krymsk": "Крымск", "kursk": "Курск", "leninsk-kuznetskiy": "Ленинск-Кузнецкий",
    "lipetsk": "Липецк", "magnitogorsk": "Магнитогорск", "maykop": "Майкоп",
    "moskovskaya_oblast_chehov": "Чехов", "moskovskaya_oblast_krasnogorsk": "Красногорск",
    "moskva": "Москва", "moskva_zelenograd": "Зеленоград", "murino": "Мурино",
    "murmansk": "Мурманск", "mytischi": "Мытищи", "naberezhnye_chelny": "Набережные Челны",
    "nefteyugansk": "Нефтеюганск", "nevinnomyssk": "Невинномысск",
    "nizhnekamsk": "Нижнекамск", "nizhnevartovsk": "Нижневартовск",
    "nizhniy_novgorod": "Нижний Новгород", "nizhniy_tagil": "Нижний Тагил",
    "norilsk": "Норильск", "novocherkassk": "Новочеркасск",
    "novokuybyshevsk": "Новокуйбышевск", "novokuznetsk": "Новокузнецк",
    "novorossiysk": "Новороссийск", "novosibirsk": "Новосибирск",
    "odintsovo": "Одинцово", "omsk": "Омск", "orehovo-zuevo": "Орехово-Зуево",
    "orel": "Орёл", "orenburg": "Оренбург", "orsk": "Орск", "perm": "Пермь",
    "petropavlovsk-kamchatskiy": "Петропавловск-Камчатский", "podolsk": "Подольск",
    "prokopevsk": "Прокопьевск", "pskov": "Псков", "pyatigorsk": "Пятигорск",
    "ramenskoe": "Раменское", "reutov": "Реутов", "rostov-na-donu": "Ростов-на-Дону",
    "ryazan": "Рязань", "rybinsk": "Рыбинск", "salavat": "Салават", "samara": "Самара",
    "sankt-peterburg": "Санкт-Петербург", "sankt-peterburg_kolpino": "Колпино",
    "sankt-peterburg_krasnoye_selo": "Красное Село", "sankt-peterburg_kronstadt": "Кронштадт",
    "saransk": "Саранск", "saratov": "Саратов", "sergiev_posad": "Сергиев Посад",
    "serpuhov": "Серпухов", "sertolovo": "Сертолово", "sevastopol": "Севастополь",
    "shahty": "Шахты", "simferopol": "Симферополь", "slavyansk-na-kubani": "Славянск-на-Кубани",
    "smolensk": "Смоленск", "sochi": "Сочи", "solnechnogorsk": "Солнечногорск",
    "staryy_oskol": "Старый Оскол", "stavropol": "Ставрополь", "sterlitamak": "Стерлитамак",
    "surgut": "Сургут", "syktyvkar": "Сыктывкар", "syzran": "Сызрань",
    "taganrog": "Таганрог", "tambov": "Тамбов", "tolyatti": "Тольятти", "tomsk": "Томск",
    "tuapse": "Туапсе", "tula": "Тула", "tver": "Тверь", "tyumen": "Тюмень", "ufa": "Уфа",
    "ulan-ude": "Улан-Удэ", "ulyanovsk": "Ульяновск", "velikiy_novgorod": "Великий Новгород",
    "verhnyaya_pyshma": "Верхняя Пышма", "vidnoe": "Видное", "vladimir": "Владимир",
    "vladivostok": "Владивосток", "volgograd": "Волгоград",
    "volgogradskaya_oblast_volzhskiy": "Волжский", "vologda": "Вологда",
    "voronezh": "Воронеж", "yalta": "Ялта", "yaroslavl": "Ярославль",
    "yuzhno-sahalinsk": "Южно-Сахалинск", "zvenigorod": "Звенигород",
    # ── ДОБРАНО СКАНОМ КОРПУСА 01.08.2026 (TERR-6/TERR-8) ────────────────────────────
    # Город не опознан → бот молча считает клиента «в черте города» и берёт РЕГИОНАЛЬНЫЙ
    # столбец прайса. По корпусу таких было 347 диалогов на 58 слагах, и среди них
    # ОСНОВНЫЕ ФИЛИАЛЫ: Чебоксары, Люберцы, Курган, Якутск, Братск, Йошкар-Ола, Волгодонск.
    # ⚠ Подмосковные и питерские пригороды дают ещё и МОСКОВСКИЙ/питерский столбец —
    # ошибка здесь стоит клиенту денег, а нам спора на месте.
    "kolomna": "Коломна", "obninsk": "Обнинск", "novoaltaysk": "Новоалтайск",
    "kurgan": "Курган", "schelkovo": "Щёлково", "cheboksary": "Чебоксары",
    "votkinsk": "Воткинск", "bratsk": "Братск", "yakutsk": "Якутск",
    "noginsk": "Ногинск", "dzerzhinsk": "Дзержинск", "lyubertsy": "Люберцы",
    "evpatoriya": "Евпатория", "volgodonsk": "Волгодонск",
    "bashkortostan_oktyabrskiy": "Октябрьский", "istra": "Истра",
    "naro-fominsk": "Наро-Фоминск", "fryazino": "Фрязино", "aleksandrov": "Александров",
    "yoshkar-ola": "Йошкар-Ола", "komsomolsk-na-amure": "Комсомольск-на-Амуре",
    "gorno-altaysk": "Горно-Алтайск", "rubtsovsk": "Рубцовск", "kubinka": "Кубинка",
    "vsevolozhsk": "Всеволожск", "kurchatov": "Курчатов", "voskresensk": "Воскресенск",
    "kurskaya_oblast_zheleznogorsk": "Железногорск", "zelenodolsk": "Зеленодольск",
    "tuchkovo": "Тучково", "balakovo": "Балаково", "pushkino": "Пушкино",
    "novomoskovsk": "Новомосковск", "dedovsk": "Дедовск", "novotroitsk": "Новотроицк",
    "elista": "Элиста", "lobnya": "Лобня", "elektrogorsk": "Электрогорск",
    "moskovskaya_oblast_troitsk": "Троицк", "elabuga": "Елабуга",
    "krasnoarmeysk": "Красноармейск", "pavlovskiy_posad": "Павловский Посад",
    "hotkovo": "Хотьково", "moskovskaya_oblast_ivanteevka": "Ивантеевка",
    "kotelniki": "Котельники", "monino": "Монино", "vyborg": "Выборг",
    "tomilino": "Томилино", "solikamsk": "Соликамск", "zhukovskiy": "Жуковский",
    # пригороды Санкт-Петербурга (в ссылке идут с префиксом города)
    "sankt-peterburg_peterhof": "Петергоф", "sankt-peterburg_sestroretsk": "Сестрорецк",
    "sankt-peterburg_pushkin": "Пушкин", "sankt-peterburg_lomonosov": "Ломоносов",
    "bugry": "Бугры", "kudrovo": "Кудрово", "novoe_devyatkino": "Новое Девяткино",
    "yanino-1": "Янино-1",
}

_NONCITY = {"profile", "brands", "user", "items", "web"}


def city_from_url(url):
    """Возвращает город (рус.) из ссылки Avito или '' если не определить."""
    m = re.search(r"avito\.ru/([a-z0-9_\-]+)/", url or "")
    if not m:
        return ""
    slug = m.group(1)
    if slug in _NONCITY:
        return ""
    if slug in CITY_BY_SLUG:
        return CITY_BY_SLUG[slug]
    # префикс региона (…_oblast_город / …_kray_город) — берём последний сегмент
    tail = re.split(r"_(?:oblast|kray|respublika|ao|kraj)_", slug)[-1]
    if tail in CITY_BY_SLUG:
        return CITY_BY_SLUG[tail]
    last = tail.split("_")[-1]
    if last in CITY_BY_SLUG:
        return CITY_BY_SLUG[last]
    return ""  # незнакомый — лучше не угадывать, бот спросит город
