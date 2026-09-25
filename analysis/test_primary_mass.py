# -*- coding: utf-8 -*-
"""МАССОВЫЙ ТЕСТ ПЕРВИЧЕК: реальные ПЕРВЫЕ сообщения из корпуса (стратифицированно по
подтипам) → первый ответ бота (прод-путь run_relay) → авточекеры всех политик заказчика.

Чекеры первого хода:
  silent      — пустой ответ на стандартной первичке (плохо, если нет явной причины)
  refuse_fp   — отказные слова без отказной темы в сообщении
  call_prom   — «наберу перед выездом» (запрещено)
  city_q      — спрашивает город (запрещено, город в объявлении)
  visit_push  — «выезд бесплатный»/«диагностика 1000» без вопроса клиента о выезде
  digit_first — цифра цены на первый ценовой вопрос (кроме обязательных озвучек и фронта-цифрами)
  echo        — 4+ слова клиента подряд в ответе (эхо)
  no_step     — нет ни вопроса, ни финала (реплика-тупик)
  dash        — тире в ответе
Запуск:  py analysis\\test_primary_mass.py [N=100] [потоков=2]
"""
import sys, io, os, re, glob, json, time, random
# ⚠ brain/ на путь ДО импорта paths: раньше `import paths` стоял первой строкой и падал с
# ModuleNotFoundError — тест запускался только если brain уже оказался на sys.path случайно.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import paths  # noqa: E402  — единая точка правды по путям
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import server, prefilter  # noqa

SRC = paths.MERGED
HERE = os.path.dirname(os.path.abspath(__file__))
N = int(sys.argv[1]) if len(sys.argv) > 1 else 100
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 2

RE_PHONE = re.compile(r"(?:\+?7|8)?[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}")
RE_URL = re.compile(r"https?://\S+")
RX_PRICE = re.compile(r"сколько\s*сто[ий]|сколько\s*(?:будет|возьм|обойд|встан)|стоимост|цен[аыу]|почём|почем|прайс", re.I)
RX_VISITQ = re.compile(r"(?:выезд|приезд|вызов|диагностик).{0,25}(?:платн|сколько|стоит|цен)|"
                       r"(?:платн|сколько|стоит).{0,25}(?:выезд|приезд|вызов)", re.I)
RX_ANKETA = re.compile(r"вот подробности|задача составлена", re.I)
RX_PHOTO = re.compile(r"🖼")
RX_MUST = re.compile(r"видеокарт|реболл|пайк|паять|гнезд\w*\s*(?:hdmi|зарядк)|priставк|приставк|playstation|xbox", re.I)
RX_FRONT_DIGITS = re.compile(r"\d+\s*(?:шт|штук|точек|отверстий|розет|м\b|м2|метров|полок|рулон)", re.I)
RX_REFUSAL_WORDS = re.compile(r"не\s*(?:занимаюсь|беру|возьмусь|работаю|смогу\s*помочь|помогу)", re.I)
RX_REFUSE_TOPIC = re.compile(r"газов|матриц|разбит\w*\s*экран|полос\w*.{0,20}экран|вскры|скупк|запчаст|выкуп|заправ|интим", re.I)
RX_SOGLAS_TOPIC = re.compile(r"кондицион|сплит|вытяжк|антенн|спутник|кронштейн|сварк|стояк|канализ|отоплен|врезк|"
                             r"плазм|кинескоп|телефон|айфон|iphone|планшет|окн[оа]|дезинфекц|высоковольт|оптоволок|"
                             r"перетяж|гипсокартон|снт|дач|за\s*город|юрлиц|безнал|тц\b|торгов|офис|школ|"
                             # ⚠ дополнено 26.08 по промахам чекера: эти темы регламент
                             # тоже отдаёт на согласование, а список здесь про них не знал,
                             # и законное молчание бота объявлялось дефектом
                             r"пищев\w*\s*принтер|экзотическ|плоттер|3d[- ]?принтер|"
                             r"широкоформатн|сублимацион", re.I)
RX_CALLPROM = re.compile(r"наберу\s*перед\s*выездом", re.I)
RX_CITYQ = re.compile(r"(?:какой|ваш)\s*(?:у вас )?город|город\s*(?:какой|подскаж|скажите)|город\s*и\s*адрес", re.I)
RX_VISIT_PUSH = re.compile(r"выезд\w*\s*(?:по городу\s*)?бесплатн|диагностик\w*\s*1000|выезд\w*.{0,15}1000", re.I)
RX_FINAL = re.compile(r"запис|до встречи|пишите|обращайтесь|сверюсь|свяжусь|разберусь", re.I)


def words(t):
    return re.findall(r"[а-яёa-z0-9]+", (t or "").lower())


def has_echo(reply, client, n=4):
    ow, cw = words(reply), words(client)
    if len(cw) < n or len(ow) < n:
        return False
    cq = {" ".join(cw[i:i + n]) for i in range(len(cw) - n + 1)}
    return any(" ".join(ow[i:i + n]) in cq for i in range(len(ow) - n + 1))


def classify(t):
    if RX_ANKETA.search(t):
        return "анкета"
    if RX_PHOTO.search(t):
        return "фото"
    if RX_PRICE.search(t):
        return "цена-первым"
    return "обычная"


def pick_sample():
    quotas = {"обычная": int(N * 0.55), "цена-первым": int(N * 0.25),
              "фото": int(N * 0.12), "анкета": max(3, N - int(N * 0.55) - int(N * 0.25) - int(N * 0.12))}
    buckets = {k: [] for k in quotas}
    files = glob.glob(os.path.join(SRC, "*.json"))
    rng = random.Random(11)
    rng.shuffle(files)
    for fp in files:
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        msgs = [m for m in (d.get("messages") or []) if (m.get("text") or "").strip()]
        if not msgs or msgs[0].get("role") != "client":
            continue
        t = msgs[0].get("text") or ""
        if len(t) < 15 or RE_PHONE.search(t):
            continue
        # чистые первички: без следов вне-чата
        if prefilter.check_existing_client(t, "", t):
            continue
        k = classify(t)
        if k in buckets and len(buckets[k]) < quotas[k]:
            title = ((d.get("meta") or {}).get("page") or {}).get("title", "") or ""
            buckets[k].append({"key": d.get("conversation_key"), "type": k,
                               "text": RE_URL.sub("🖼 (фото)", t)[:600], "title": title[:80]})
        if all(len(v) >= quotas[k2] for k2, v in buckets.items()):
            break
    out = [x for v in buckets.values() for x in v]
    rng.shuffle(out)
    return out


# ⚠ ПРЕДОХРАНИТЕЛЬ ПО СЕТИ (26.08). Набор не «висел» — он медленно умирал: 100 вызовов
# в два потока, у каждого свои три попытки с паузами, и при пропавшем DNS это ровно
# двенадцать минут перемалывания заведомо мёртвых запросов. Один такой сбой сегодня
# стоил получаса ожидания посреди полного прогона.
# Таймаут тут не помогает: он есть (90 с на запрос) и не срабатывает — запросы падают
# быстро. Помогает другое: если ПОДРЯД несколько вызовов умерли именно СЕТЕВОЙ ошибкой,
# сети нет, и остальные 97 ничего не покажут. Останавливаемся и говорим об этом вслух.
#
# ⚠ СЧИТАЕМ ПОДРЯД, А НЕ ВСЕГО. Одиночный сетевой сбой на сотне вызовов — норма жизни
# (429, обрыв, таймаут провайдера), и ронять из-за него прогон нельзя.
_ПОДРЯД_ДО_ОСТАНОВКИ = 3
_СЕТЕВАЯ = re.compile(r"Сеть:|nodename nor servname|Temporary failure in name resolution|"
                      r"Connection refused|Network is unreachable|timed out", re.IGNORECASE)


class ОбрывСети(RuntimeError):
    """Сети нет — остальные вызовы ничего не покажут, прогон дальше бессмыслен."""


_сеть = {"подряд": 0}


def check_one(item, persona):
    if _сеть["подряд"] >= _ПОДРЯД_ДО_ОСТАНОВКИ:
        return {**item, "reply": "(пропущено: сети нет)", "flags": ["network"]}
    t = item["text"]
    try:
        res = server.run_relay([{"role": "client", "text": t}],
                               known={"persona": persona, "title": item.get("title") or ""})
    except Exception as e:
        if _СЕТЕВАЯ.search(str(e)):
            _сеть["подряд"] += 1
            return {**item, "reply": "(СЕТЬ %s)" % e, "flags": ["network"]}
        _сеть["подряд"] = 0
        return {**item, "reply": "(ОШИБКА %s)" % e, "flags": ["error"]}
    _сеть["подряд"] = 0
    reply = (res.get("reply") or "").strip()
    flags = []
    low = reply.lower()
    if not reply:
        # пустота законна для соглас/жалоб/existing — на первичках почти всегда подозрительна
        if not RX_SOGLAS_TOPIC.search(t):
            flags.append("silent")
    else:
        if RX_REFUSAL_WORDS.search(low) and not RX_REFUSE_TOPIC.search(t.lower()) \
                and not RX_SOGLAS_TOPIC.search(t.lower()):
            flags.append("refuse_fp")
        if RX_CALLPROM.search(low):
            flags.append("call_prom")
        if RX_CITYQ.search(low):
            flags.append("city_q")
        if RX_VISIT_PUSH.search(low) and not RX_VISITQ.search(t):
            flags.append("visit_push")
        if RX_PRICE.search(t) and re.search(r"\bот\s*\d{3,}|\d{3,}\s*р", low) \
                and not RX_MUST.search(t) and not RX_FRONT_DIGITS.search(t):
            flags.append("digit_first")
        if has_echo(reply, t):
            flags.append("echo")
        # ⚠ ОТКАЗ — ЗАКОНЧЕННЫЙ ХОД, А НЕ ТУПИК (правка 26.08). Чекер объявлял «ход без
        # шага» любую реплику без «?»: «Здравствуйте, матрицами не занимаюсь», «С заменой
        # матрицы не помогу, её менять невыгодно». Регламент велит по таким темам
        # отказывать, и вопрос после отказа был бы ХУЖЕ — он предлагает продолжать
        # разговор, который мы закрываем. Та же правка сделана в эталоне: там из 17
        # «дефектов» отказами оказались все 17.
        # ⚠ И ПРОСЬБА ДАННЫХ БЕЗ «?» — ТОЖЕ ШАГ: три формулировки из четырёх в номерном
        # пуле знака вопроса не имеют намеренно, живые мастера пишут «И номер ваш
        # подскажите» без него (замер пунктуации по 14 000 реплик).
        if "?" not in reply and not RX_FINAL.search(low) \
                and not RX_REFUSAL_WORDS.search(low) \
                and not re.search(r"не\s+помогу|не\s+занимаюсь|не\s+берусь|невыгодн|"
                                  r"дороже\s+нового|не\s+мой\s+профиль", low) \
                and not re.search(r"номер\w*\s+(?:ваш|для\s+связи|телефона|подскажите|"
                                  r"оставьте)|номерок|оставьте\s+номер", low) \
                and not re.search(r"готов\s+подъехать|могу\s+подъехать|давайте\s+подъеду|"
                                  r"подъеду\s+и\s+(?:гля|посмотр|отвеч)|заеду\s+и\s+"
                                  r"(?:гля|посмотр|отвеч)", low):
            flags.append("no_step")
        if "—" in reply or " – " in reply:
            flags.append("dash")
    return {**item, "reply": reply, "routed": (res.get("flag") or {}).get("kind"),
            "handoff": bool(res.get("handoff")), "flags": flags}


def main():
    sample = pick_sample()
    print("Первичек в тесте: %d (%s)" % (len(sample),
          ", ".join("%s=%d" % (k, sum(1 for s in sample if s["type"] == k))
                    for k in ("обычная", "цена-первым", "фото", "анкета"))))
    t0 = time.time()
    results = []
    personas = ["p%d" % (i % 10 + 1) for i in range(len(sample))]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(check_one, s, personas[i]): i for i, s in enumerate(sample)}
        done = 0
        for fu in as_completed(futs):
            results.append(fu.result())
            done += 1
            if done % 20 == 0:
                print("  %d/%d (%.0f сек)" % (done, len(sample), time.time() - t0))
    сетевых = sum(1 for r in results if "network" in r["flags"])
    if _сеть["подряд"] >= _ПОДРЯД_ДО_ОСТАНОВКИ:
        print("\n" + "=" * 60)
        print("ПРОГОН ОСТАНОВЛЕН: %d вызовов подряд умерли сетевой ошибкой." % _ПОДРЯД_ДО_ОСТАНОВКИ)
        print("Сети до api.anthropic.com нет — остальные %d вызовов ничего не покажут."
              % сетевых)
        print("Проверьте связь и повторите:  uv run python analysis/test_primary_mass.py")
        print("=" * 60)
        sys.exit(3)
    bad = [r for r in results if r["flags"]]
    counts = {}
    for r in bad:
        for f in r["flags"]:
            counts[f] = counts.get(f, 0) + 1
    print("\nЧИСТЫХ: %d / %d (%.0f%%) за %.0f сек" % (len(results) - len(bad), len(results),
          100.0 * (len(results) - len(bad)) / max(1, len(results)), time.time() - t0))
    print("Флаги:", counts or "нет")
    with open(os.path.join(HERE, "test_primary_mass.jsonl"), "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("\nПРОБЛЕМНЫЕ (все, до 25):")
    for r in bad[:25]:
        print(" [%s] %s" % (",".join(r["flags"]), r["text"][:80].replace("\n", " ")))
        print("    → %s" % (r["reply"] or "(пусто)").replace("\n", " / ")[:150])
    # ⚠ ОТКАЗ ШЛЮЗА — ЭТО ПРОВАЛ ПРОГОНА, А НЕ ЕГО РЕЗУЛЬТАТ (F-002, этап 0 программы
    # обучения). Раньше набор глотал ошибку модели и выходил кодом 0: в журнале
    # значилось «(ОШИБКА …)», а наружу шёл успех. Массовая проверка первичных
    # обращений при этом не выполнялась вовсе.
    пустых = sum(1 for r in results if not (r.get("reply") or "").strip())
    if not results or пустых > len(results) * 0.5:
        print("\nПРОВАЛ: %d из %d обращений без ответа — прогон недействителен"
              % (пустых, len(results)))
        sys.exit(2)
    # ⚠ ЭТО ЗАМЕР, А НЕ ВОРОТА — И ЭТО ПОНЯТО НА ПРАКТИКЕ 26.08. Набор бьёт ЖИВОЙ моделью
    # по ста случайным первичкам, и формулировка ответа меняется от прогона к прогону:
    # четыре запуска подряд дали четыре разных набора флагов на одних и тех же входах.
    # Правило «любой флаг = провал» превращало это в монетку: красный не значил поломки,
    # а зелёный не значил исправности, и читать его переставали.
    # Ворота теперь по ДОЛЕ чистых. Порог 90 % при наблюдаемых 95-97 %: падение ниже —
    # это уже не разброс формулировок, а сломанный слой.
    # Сами флаги печатаются всегда: их ценность в чтении глазами, а не в коде возврата.
    ПОРОГ = 90
    доля = 100.0 * (len(results) - len(bad)) / max(1, len(results))
    if доля < ПОРОГ:
        print("ПРОВАЛ: чистых %.0f %% при пороге %d %%" % (доля, ПОРОГ))
        sys.exit(1)
    if bad:
        print("(флаги выше — материал для чтения, не провал: порог %d %%, сейчас %.0f %%)"
              % (ПОРОГ, доля))
    sys.exit(0)


if __name__ == "__main__":
    main()
