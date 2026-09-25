# -*- coding: utf-8 -*-
"""КЭШ ГОТОВЫХ ОТВЕТОВ: ключ, срок годности и гигиена файла (правка 24.08).

ЧТО БЫЛО. В ключ входил ВЕСЬ плейбук — `prompt.build_system()`, 103 479 знаков. Любая
его правка меняла все ключи разом, а плейбук правят почти каждый день: 19 коммитов за
20 дней. Кэш жил в среднем сутки, а чтобы он начал отдавать ответы, одному ключу нужно
накопить хотя бы ДВА варианта — единственный не отдаётся никогда (анти-отпечаток на 35
аккаунтах). Итог по боевому журналу воронки: попадание 0,71% ходов (42 из 5881) при
3956 записях в файле. То есть кэша фактически не было, а платили за него как за живой.

ЧТО СТАЛО. Плейбук из ключа убран, протухание ловится СРОКОМ ГОДНОСТИ записи. Это
честнее по сути: важно не «тот же ли плейбук», а «не устарела ли формулировка».
Жёсткие правила от этого не страдают — на попадании реплика проходит те же сорок
гардов _postprocess_reply, что и свежая; стареет только формулировка.

Офлайн, 0 токенов. Запуск: python3 analysis/test_answer_cache.py
"""
import io
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "brain"))
import prompt                                       # noqa: E402
import server as s                                  # noqa: E402

СЛУЧАИ = []


def chk(имя, получили, ждали):
    СЛУЧАИ.append((имя, получили, ждали))


ДИАЛОГ = [{"role": "client", "text": "Здравствуйте, не морозит холодильник"}]
ИЗВЕСТНО = {"city": "Москва", "direction": "bt", "title": "", "persona": "",
            "partner": "", "white": False}

# ── 1. ПРАВКА ПЛЕЙБУКА БОЛЬШЕ НЕ ОБНУЛЯЕТ КЭШ ───────────────────────────────
_ключ_до = s._answer_key(ДИАЛОГ, ИЗВЕСТНО)
_настоящий_плейбук = prompt.build_system
try:
    prompt.build_system = lambda *a, **k: _настоящий_плейбук() + "\n⛔ НОВОЕ ПРАВИЛО ПЛЕЙБУКА"
    _ключ_после = s._answer_key(ДИАЛОГ, ИЗВЕСТНО)
finally:
    prompt.build_system = _настоящий_плейбук
chk("правка плейбука не меняет ключ", _ключ_после, _ключ_до)

# ...а вот то, от чего ответ ДЕЙСТВИТЕЛЬНО зависит, ключ менять обязано
chk("город меняет ключ",
    s._answer_key(ДИАЛОГ, dict(ИЗВЕСТНО, city="Якутск")) != _ключ_до, True)
chk("направление меняет ключ",
    s._answer_key(ДИАЛОГ, dict(ИЗВЕСТНО, direction="kp")) != _ключ_до, True)
chk("реплика клиента меняет ключ",
    s._answer_key([{"role": "client", "text": "течёт стиралка"}], ИЗВЕСТНО) != _ключ_до, True)
chk("белый партнёр меняет ключ",
    s._answer_key(ДИАЛОГ, dict(ИЗВЕСТНО, white=True)) != _ключ_до, True)

# ── 2. СРОК ГОДНОСТИ ────────────────────────────────────────────────────────
_сейчас = time.time()
chk("свежая запись годится",
    s._cache_reusable({"reply": "гляну, что случилось", "t": _сейчас}, now=_сейчас), True)
chk("запись на границе срока ещё годится",
    s._cache_reusable({"reply": "гляну", "t": _сейчас - s._CACHE_TTL_S + 60}, now=_сейчас), True)
chk("протухшая запись не годится",
    s._cache_reusable({"reply": "гляну", "t": _сейчас - s._CACHE_TTL_S - 60}, now=_сейчас), False)
# ⚠ ЗАПИСЬ БЕЗ ОТМЕТКИ ВРЕМЕНИ — ПРОТУХШАЯ. Такие остались от прежнего формата, и все они
# лежат под ключами, в которые входил плейбук: совпасть уже не могут в принципе.
chk("запись старого формата не годится",
    s._cache_reusable({"reply": "гляну"}, now=_сейчас), False)
# прежние причины отказа никуда не делись
chk("пустая реплика не годится", s._cache_reusable({"reply": "  ", "t": _сейчас}, now=_сейчас), False)
chk("реплика с абсолютным часом не годится",
    s._cache_reusable({"reply": "буду сегодня к 15:00", "t": _сейчас}, now=_сейчас), False)

# ── 3. ЗАПИСЬ САМА СТАВИТ ОТМЕТКУ ВРЕМЕНИ ───────────────────────────────────
_путь = os.environ.get("LEADBOT_ANSWER_CACHE")
if _путь:                       # под офлайн-прогоном пишем в свой файл, не в боевой
    _к = "проверка-отметки-времени"
    s._answer_cache_put(_к, {"reply": "гляну, что случилось", "category": "bt"})
    _записи = s._ANSWER_CACHE.get(_к) or []
    chk("запись получила отметку времени",
        bool(_записи) and isinstance(_записи[-1].get("t"), int), True)
    chk("свежая запись сразу годна к выдаче",
        s._cache_reusable(_записи[-1]) if _записи else False, True)

# ── 4. ГИГИЕНА ФАЙЛА ────────────────────────────────────────────────────────
# Наборы гоняют боевой путь целиком и дописывали свои заглушки в БОЕВОЙ кэш: за сутки
# 554 фикстуры в файле, по которому потом считаются замеры качества.
chk("путь кэша переопределяется окружением",
    "LEADBOT_ANSWER_CACHE" in open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "brain", "server.py"),
        encoding="utf-8").read(), True)
chk("офлайн-прогон уводит кэш в свой файл",
    "LEADBOT_ANSWER_CACHE" in open(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "_run_offline.sh"),
        encoding="utf-8").read(), True)

# ── КЭШ УПЛОТНЯЕТСЯ САМ, А НЕ РАСТЁТ ВЕЧНО (замер боевого 25.08) ────────────
# Файл дописывается при каждом ответе, а протухшую запись никто не убирал:
# `_cache_reusable` отбрасывает её на ВЫДАЧЕ, но в память она всё равно попадала и
# жила до перезапуска. Замер боевого кэша: 1030 записей, 241 КБ, годных к выдаче НОЛЬ —
# то есть весь файл был мёртвым грузом.
import json as _json                                              # noqa: E402
import tempfile as _tempfile                                      # noqa: E402
import time as _time                                              # noqa: E402

_ф = _tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
for _i in range(30):
    _ф.write(_json.dumps({"k": "мертвый%d" % _i, "v": {"reply": "старое %d" % _i}}) + "\n")
for _i in range(4):
    _ф.write(_json.dumps({"k": "живой%d" % _i,
                          "v": {"reply": "свежее %d" % _i, "t": int(_time.time())}}) + "\n")
_ф.close()
_прежний_путь, _прежний_кэш = s._ANSWER_CACHE_PATH, dict(s._ANSWER_CACHE)
try:
    s._ANSWER_CACHE.clear()
    s._ANSWER_CACHE_PATH = _ф.name
    s._load_answer_cache()
    chk("мёртвые записи в память не грузятся", len(s._ANSWER_CACHE), 4)
    chk("файл уплотнён", len(io.open(_ф.name, encoding="utf-8").readlines()), 4)
    # ⚠ ЖИВОЕ ПРИ ЭТОМ ЦЕЛО — иначе уплотнение стирало бы рабочий кэш.
    chk("живые записи уцелели",
        all(("живой%d" % i) in s._ANSWER_CACHE for i in range(4)), True)
finally:
    s._ANSWER_CACHE_PATH = _прежний_путь
    s._ANSWER_CACHE.clear()
    s._ANSWER_CACHE.update(_прежний_кэш)
    try:
        os.unlink(_ф.name)
    except OSError:
        pass

# ⚠ УПЛОТНЯЕМ ТОЛЬКО КОГДА МУСОРА БОЛЬШЕ ПОЛОВИНЫ: переписывать файл на каждом старте
# ради пары протухших записей — лишний риск обрыва там, где выигрыша нет.
_ф2 = _tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
for _i in range(30):
    _ф2.write(_json.dumps({"k": "живой%d" % _i,
                           "v": {"reply": "свежее %d" % _i, "t": int(_time.time())}}) + "\n")
_ф2.write(_json.dumps({"k": "мертвый", "v": {"reply": "старое"}}) + "\n")
_ф2.close()
_прежний_путь2 = s._ANSWER_CACHE_PATH
try:
    s._ANSWER_CACHE.clear()
    s._ANSWER_CACHE_PATH = _ф2.name
    s._load_answer_cache()
    chk("здоровый файл не переписывается",
        len(io.open(_ф2.name, encoding="utf-8").readlines()), 31)
finally:
    s._ANSWER_CACHE_PATH = _прежний_путь2
    s._ANSWER_CACHE.clear()
    s._ANSWER_CACHE.update(_прежний_кэш)
    try:
        os.unlink(_ф2.name)
    except OSError:
        pass

плохо = [(n, g, w) for n, g, w in СЛУЧАИ if g != w]
print("=" * 60)
for n, g, w in плохо:
    print("ПРОВАЛ  %-44s получили %r, ждали %r" % (n, g, w))
print("Кэш ответов: %d/%d" % (len(СЛУЧАИ) - len(плохо), len(СЛУЧАИ)))
sys.exit(1 if плохо else 0)
