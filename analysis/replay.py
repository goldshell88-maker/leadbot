# -*- coding: utf-8 -*-
"""ПРОГОН ПО ЖИВЫМ ДИАЛОГАМ: бот отвечает там же, где отвечал человек.

ЗАЧЕМ ЭТО, А НЕ ТОЛЬКО БОЙ. В бою оператор может не ответить вовсе — диалог висит в
очереди, и сравнивать бота не с чем. В корпусе ответ живого человека есть ВСЕГДА: он
уже написан. Поэтому учиться удобнее здесь: на каждом ходе видно, что спросил клиент,
что на это ответил живой мастер, что ответил бы бот и на чём он это решил.

⚠ БОТ НЕ ВИДИТ БУДУЩЕГО. История отдаётся ровно до текущего момента; ответ оператора
показывается ПОСЛЕ того, как бот высказался, и в его вход не попадает.

⚠ ПОДСКАЗКИ БОТА ОТДАЮТСЯ ЕМУ ОТДЕЛЬНЫМ ПОЛЕМ, как это делает панель: в режиме
подсказки его реплики лежат заметками и в переписку не возвращаются. Без этого бот
здоровался бы заново на каждом ходе — проверено, ровно так и было.

ДВА РЕЖИМА:
    --кассета   ответы модели берутся из записи эталона (analysis/BENCHMARK/кассета.json).
                Ноль токенов, воспроизводимо, но только по замороженной выборке.
    без ключа   живые вызовы модели по свежим диалогам корпуса. Стоит денег.

Вывод — строки JSON теневого журнала, их рисует analysis/watch.py:

    uv run python analysis/replay.py 5 --кассета | uv run python analysis/watch.py
"""
import io
import json
import os
import sys
import time

КОРЕНЬ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(КОРЕНЬ, "brain"))
sys.path.insert(0, os.path.join(КОРЕНЬ, "analysis"))
# ⚠ ЖУРНАЛ БОТА ОТКЛЮЧАЕМ: прогон не должен подмешиваться в боевой файл наблюдения.
os.environ.setdefault("LEADBOT_SHADOW", "0")
# ⚠ И КЭШ ОТВЕТОВ ТОЖЕ СВОЙ: учебный прогон не имеет права засорять боевой.
os.environ.setdefault("LEADBOT_ANSWER_CACHE",
                      os.path.join(КОРЕНЬ, "data", "replay_cache.jsonl"))

# ⚠ ЧУЖОЙ ВЫВОД УВОДИМ В stderr, НАШ ОСТАВЛЯЕМ В stdout. Бот пишет свой журнал
# (стоимость модели, эскалации) обычным print — и эти строки попадали в ту же трубу,
# что и записи прогона. Смотрелка честно пыталась их нарисовать и выдавала пустые
# карточки. Разделяем каналы здесь, а не глушим журнал: он нужен, просто не в этой трубе.
_НАШ_ВЫВОД = sys.stdout
sys.stdout = sys.stderr

import claude_api                                       # noqa: E402
import prefilter                                        # noqa: E402
import server                                           # noqa: E402
from benchmark import Кассета, КАССЕТА, выборка          # noqa: E402
from train_mode import из_корпуса                       # noqa: E402


def вывести(**поля):
    поля.setdefault("вид", "ход")
    поля.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    _НАШ_ВЫВОД.write(json.dumps(поля, ensure_ascii=False) + "\n")
    _НАШ_ВЫВОД.flush()


def прогнать(d, номер, пауза):
    история, свои = [], []
    for i, m in enumerate(d["реплики"]):
        история.append(m)
        if m["role"] != "client":
            continue
        # ответ живого — его ближайшая следующая реплика
        живой = ""
        for сл in d["реплики"][i + 1:]:
            if сл["role"] == "operator":
                живой = сл["text"]
                break
            if сл["role"] == "client":
                break
        if not живой:
            continue
        t0 = time.time()
        try:
            r = server.run_relay([dict(x) for x in история],
                                 known={"city": d.get("город") or "Москва",
                                        "visitor": "", "title": "",
                                        "prior_self": list(свои)})
        except Exception as e:
            вывести(чат="учебный-%d" % номер, город=d.get("город") or "",
                    клиент=m["text"], оператор=живой, бот="",
                    почему="ход не собрался: %s" % e)
            continue
        реплика = (r.get("reply_text") or r.get("reply") or "").strip()
        вывести(чат="учебный-%d" % номер,
                город=d.get("город") or "",
                направление=prefilter.lock_direction(
                    [x["text"] for x in история if x["role"] == "client"], "") or "",
                тема="учебный прогон по корпусу",
                клиент=m["text"],
                оператор=живой,
                подсказка_до=свои[-1] if свои else "",
                бот=реплика,
                стадия=getattr(server._ХОД, "стадия", ""),
                слой=r.get("model") or "",
                почему=r.get("note") or "",
                передал=bool(r.get("notify_human")),
                причина=(r.get("escalation") or {}).get("reason") or "",
                не_хватает=list(r.get("missing") or []),
                заявка=bool(r.get("lead_ready")),
                ходов=len(история),
                мс=int((time.time() - t0) * 1000))
        if реплика:
            свои.append(реплика)
        if пауза:
            time.sleep(пауза)


def главное():
    сколько = 5
    пауза = 0.0
    по_кассете = "--кассета" in sys.argv
    for i, a in enumerate(sys.argv[1:], 1):
        if a.isdigit():
            сколько = int(a)
        if a == "--пауза" and i + 1 < len(sys.argv):
            пауза = float(sys.argv[i + 1])
    диалоги = (выборка() if по_кассете else из_корпуса(сколько))[:сколько]
    кассета = Кассета(КАССЕТА, "сухой" if по_кассете else "живой")
    настоящая = кассета.обернуть()
    try:
        for n, d in enumerate(диалоги, 1):
            прогнать(d, n, пауза)
    finally:
        claude_api.messages = настоящая
    if по_кассете and кассета.промахов:
        вывести(вид="служебное",
                почему="ходов без записи в кассете: %d — обновите её "
                       "(uv run python analysis/benchmark.py --записать)" % кассета.промахов)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(главное())
    except KeyboardInterrupt:
        pass
