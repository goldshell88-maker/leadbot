# -*- coding: utf-8 -*-
"""ЗАМЕР ЗОНЫ ДАЛЬНЕГО ВЫЕЗДА 36 КМ (правило заказчика 01.08.2026, только направление КП).

Заказчик: «для КП можно создать заявку без согласования как дальний выезд менее 36 км
от основного филиала, для МНЧ и БТ такого правила нет — либо основной филиал, либо спутник».

Чтобы бот мог решать это БЕЗ геокодера в рантайме, зону считаем заранее и кладём в
brain/near36.json: для каждого основного филиала — список населённых пунктов в радиусе
36 км включительно, с расстоянием.

Данные открытые, ключи не нужны:
  • координаты филиалов — Nominatim (OpenStreetMap), не чаще 1 запроса в секунду;
  • населённые пункты вокруг — Overpass API, place=city|town|village|hamlet.

Запуск (долгий, ~15-25 мин — гонять отвязанно):
    py analysis\\measure_36km.py            — полный замер
    py analysis\\measure_36km.py --coords   — только координаты филиалов
    py analysis\\measure_36km.py --resume   — продолжить с места обрыва
"""
import io
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "brain"))
import filials  # noqa: E402

OUT = os.path.join(ROOT, "brain", "near36.json")
COORDS = os.path.join(ROOT, "analysis", "_filial_coords.json")
RADIUS_M = filials.FAR_TRIP_KM * 1000

UA = {"User-Agent": "leadbot-territory/1.0 (internal dispatch tool; contact via project owner)"}
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = "https://overpass-api.de/api/interpreter"

# Одноимённых городов в России много (Железногорск, Королёв, Кировск…), поэтому у неоднозначных
# подсказываем регион. Список пополняется по итогам проверки вывода.
REGION_HINT = {
    "Железногорск": "Курская область",
    "Королёв": "Московская область",
    "Красногорск": "Московская область",
    "Одинцово": "Московская область",
    "Мытищи": "Московская область",
    "Видное": "Московская область",
    "Домодедово": "Московская область",
    "Балашиха": "Московская область",
    "Химки": "Московская область",
    "Люберцы": "Московская область",
    "Подольск": "Московская область",
    "Долгопрудный": "Московская область",
    "Коммунарка": "Москва",
    "Зеленоград": "Москва",
    "Электросталь": "Московская область",
    "Ногинск": "Московская область",
    "Раменское": "Московская область",
    "Жуковский": "Московская область",
    "Сергиев Посад": "Московская область",
    "Орехово-Зуево": "Московская область",
    "Серпухов": "Московская область",
    "Октябрьский": "Республика Башкортостан",
    "Кировск": "Ленинградская область",
    "Губкин": "Белгородская область",
    "Артём": "Приморский край",
}


def haversine_km(a_lat, a_lon, b_lat, b_lon):
    r = 6371.0088
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _get(url, data=None, timeout=90, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:                      # noqa: BLE001 — сеть, ретраим молча
            last = e
            time.sleep(3 + 4 * i)
    print("   ! сеть: %s" % str(last)[:120])
    return None


def geocode(city):
    """Координаты города через Nominatim. Вежливая пауза 1.1 с — требование их правил."""
    q = city + (", " + REGION_HINT[city] if city in REGION_HINT else "") + ", Россия"
    url = NOMINATIM + "?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": 1, "accept-language": "ru"})
    body = _get(url, timeout=45)
    time.sleep(1.1)
    if not body:
        return None
    try:
        arr = json.loads(body)
    except Exception:
        return None
    if not arr:
        return None
    return {"lat": float(arr[0]["lat"]), "lon": float(arr[0]["lon"]),
            "display": arr[0].get("display_name", "")}


def load_coords(resume=True):
    have = {}
    if resume and os.path.exists(COORDS):
        with open(COORDS, encoding="utf-8") as f:
            have = json.load(f)
    # правило 36 км действует только на КП — значит и замер только по филиалам КП
    cities = list(filials.MAIN_KP)
    todo = [c for c in cities if c not in have]
    print("Координаты филиалов: есть %d, нужно ещё %d" % (len(have), len(todo)))
    for i, c in enumerate(todo, 1):
        g = geocode(c)
        if g:
            have[c] = g
            print("  [%3d/%3d] %-26s %.4f, %.4f  %s" % (i, len(todo), c, g["lat"], g["lon"],
                                                        g["display"][:60]))
        else:
            print("  [%3d/%3d] %-26s НЕ НАЙДЕН" % (i, len(todo), c))
        if i % 10 == 0:
            with open(COORDS, "w", encoding="utf-8") as f:
                json.dump(have, f, ensure_ascii=False, indent=1)
    with open(COORDS, "w", encoding="utf-8") as f:
        json.dump(have, f, ensure_ascii=False, indent=1)
    return have


# ⚠ ТОЛЬКО населённые пункты. place=suburb (районы внутри города) сюда брать НЕЛЬЗЯ: оттуда
# лезут «Центр», «Кирова», «5-й микрорайон», «Советский район» — по названию они совпадают
# с обычными городскими адресами, и запасной поиск по имени давал бы ложный «дальний выезд».
_Q = ('[out:json][timeout:120];'
      'node["place"~"^(city|town|village|hamlet)$"]["name"](around:%d,%.6f,%.6f);'
      'out body;')


def around(lat, lon):
    q = _Q % (RADIUS_M, lat, lon)
    body = _get(OVERPASS, data=q.encode("utf-8"), timeout=180)
    if not body:
        return None
    try:
        d = json.loads(body)
    except Exception:
        return None
    res = []
    for el in d.get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name:ru") or tags.get("name")
        if not name:
            continue
        res.append({"name": name, "place": tags.get("place"),
                    "lat": el.get("lat"), "lon": el.get("lon")})
    return res


def main():
    only_coords = "--coords" in sys.argv
    coords = load_coords()
    if only_coords:
        return 0
    out = {}
    if "--resume" in sys.argv and os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            out = json.load(f).get("filials", {})
    todo = [c for c in coords if c not in out]
    print("\nЗамер радиуса %d км: осталось филиалов %d" % (filials.FAR_TRIP_KM, len(todo)))
    for i, city in enumerate(todo, 1):
        g = coords[city]
        pts = around(g["lat"], g["lon"])
        if pts is None:
            print("  [%3d/%3d] %-26s пропуск (сеть)" % (i, len(todo), city))
            continue
        near = []
        for p in pts:
            if p["lat"] is None or p["lon"] is None:
                continue
            km = haversine_km(g["lat"], g["lon"], p["lat"], p["lon"])
            if km <= filials.FAR_TRIP_KM + 1e-9:
                near.append({"name": p["name"], "place": p["place"], "km": round(km, 1)})
        near.sort(key=lambda x: x["km"])
        out[city] = {"lat": g["lat"], "lon": g["lon"], "count": len(near), "settlements": near}
        print("  [%3d/%3d] %-26s населённых пунктов ≤%d км: %d" %
              (i, len(todo), city, filials.FAR_TRIP_KM, len(near)))
        with open(OUT, "w", encoding="utf-8") as f:
            json.dump({"radius_km": filials.FAR_TRIP_KM,
                       "note": "Только для КП: дальний выезд без согласования. МНЧ/БТ — не применять.",
                       "filials": out}, f, ensure_ascii=False)
        time.sleep(1.5)                     # вежливость к Overpass
    total = sum(v["count"] for v in out.values())
    print("\nГотово: филиалов %d, населённых пунктов в зоне %d км — %d" %
          (len(out), filials.FAR_TRIP_KM, total))
    print("Файл: %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
