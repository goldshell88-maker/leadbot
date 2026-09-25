# -*- coding: utf-8 -*-
"""Проигрывает сценарии-клиенты (review_scenarios.json) против ПРОД-пути бота
(run_relay: локальные роутеры + модель + все страховки) и складывает готовые
диалоги в review_dialogs.json для страницы оценки http://localhost:8789/review.

Клиента играет детерминированный отвечатель: на вопросы бота отвечает фактами
сценария (адрес/время/телефон/уточнения), между делом давит на цену и вставляет
свои реплики-инициативы, финал по end_intent (запись/подумаю/пропал/отказ).

Запуск:  py analysis\\gen_review_dialogs.py [потоков=2] [id id ...]
(если переданы id — перегенерируются ТОЛЬКО они и вливаются в существующий review_dialogs.json)
"""
import sys, io, os, json, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import server  # noqa

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "review_scenarios.json")
OUT = os.path.join(HERE, "review_dialogs.json")
WORKERS = int(sys.argv[1]) if len(sys.argv) > 1 else 2   # шлюз пускает максимум 2 параллельно!
ONLY = set(sys.argv[2:])                                 # перегенерация только этих id (вливается в старый файл)

PERSONAS = ["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9", "p10"]

RX_ADDR = re.compile(r"адрес|куда (?:нужно )?(?:будет )?(?:при|подъ)ехать|куда подъехать|куда к вам", re.I)
RX_EXTRA = re.compile(r"квартир|подъезд|этаж|номер дома|полный адрес", re.I)
RX_TIME = re.compile(r"когда вам|когда удобно|во сколько|какое время|время вас записать|удобно принять|когда помощь|когда нужна", re.I)
RX_PHONE = re.compile(r"номер(?:ок)?(?![а-яё])|телефон", re.I)
# финал записи — только «записал ВАС»/«до встречи»; «записал адрес» — не финал (обрывало диалоги в 1-й партии)
RX_BOOKED = re.compile(r"запис\w*\s+вас|до встречи", re.I)
RX_PRICE_CTX = re.compile(r"цен|стоимост|на месте|осмотр|от \d|выезд|диагностик", re.I)
RX_PHONE_WHY = re.compile(r"не выезжаю|никто не (?:открыл|встре)|бывало|для связи|перед выездом|наберу", re.I)

THANKS = ["Спасибо!", "Хорошо, жду", "Спасибо, договорились", "Ок, до встречи", "Хорошо"]
THINK = ["ладно, я подумаю", "спасибо, подумаю и напишу", "понял, надо подумать"]
REFUSE = ["нет, спасибо, не надо", "не, я передумал", "дорого выходит, откажусь"]


def _bot_meta(res):
    m = {}
    if res.get("model"):
        m["model"] = res["model"]
    if res.get("flag"):
        m["flag"] = res["flag"]
    if res.get("enforced"):
        m["enforced"] = res["enforced"]
    if res.get("to_operator"):
        m["to_operator"] = True
    if res.get("handoff"):
        m["handoff"] = True
    return m


def play(brief, persona):
    facts = brief.get("facts") or {}
    pushes = list(brief.get("price_push_texts") or [])[: int(brief.get("price_pushes") or 0)]
    followups = list(brief.get("followup_messages") or [])
    quali = list(brief.get("quali_answers") or [])
    policy = brief.get("phone_policy") or "immediate"
    end_intent = brief.get("end_intent") or "book"
    gave = {"addr": False, "extra": False, "time": False, "phone": False}
    phone_pushback_done = False
    ended_by = ""

    msgs = [{"role": "client", "text": brief.get("first_message") or ""}]
    out_msgs = [dict(msgs[0])]
    known = {"city": brief.get("city") or "", "persona": persona}

    for turn in range(9):
        res, last_err = None, None
        for attempt in range(6):        # шлюз строг к параллельности (429) — ретраим с паузой
            try:
                res = server.run_relay(msgs, known=known)
                last_err = None
                break
            except Exception as e:
                last_err = e
                if "429" in str(e) or "rate_limit" in str(e) or "Слишком много" in str(e):
                    time.sleep(4 + attempt * 3)
                    continue
                time.sleep(2)
        if last_err is not None:
            out_msgs.append({"role": "bot", "text": "(ОШИБКА: %s)" % last_err, "meta": {"error": True}})
            ended_by = "error"
            break
        reply = (res.get("reply") or "").strip()
        meta = _bot_meta(res)
        if not reply:
            out_msgs.append({"role": "bot", "text": "", "meta": meta})
            ended_by = "to_operator" if res.get("to_operator") else "empty"
            break
        out_msgs.append({"role": "bot", "text": reply, "meta": meta})
        msgs.append({"role": "bot", "text": reply})

        # --- решаем следующий ход клиента ---
        if RX_BOOKED.search(reply) and (gave["phone"] or policy == "refuse"):
            nxt = THANKS[hash(brief["id"]) % len(THANKS)]
            out_msgs.append({"role": "client", "text": nxt})
            ended_by = "booked"
            break

        asked_addr = bool(RX_ADDR.search(reply)) and not gave["addr"]
        asked_extra = bool(RX_EXTRA.search(reply)) and not gave["extra"]
        asked_time = bool(RX_TIME.search(reply)) and not gave["time"]
        asked_phone = bool(RX_PHONE.search(reply)) and not gave["phone"] and policy != "refuse"

        # клиент сперва давит цену, потом отдаёт данные (как в жизни)
        if pushes and RX_PRICE_CTX.search(reply):
            nxt = pushes.pop(0)
        else:
            parts = []
            if asked_phone and policy == "after_push" and not phone_pushback_done:
                phone_pushback_done = True
                parts = ["а зачем вам номер?"]
            else:
                if asked_addr:
                    parts.append(facts.get("address_street") or "")
                    gave["addr"] = True
                    if asked_extra:
                        parts.append(facts.get("address_extra") or "")
                        gave["extra"] = True
                elif asked_extra and gave["addr"]:
                    parts.append(facts.get("address_extra") or "")
                    gave["extra"] = True
                if asked_time:
                    parts.append(facts.get("time_pref") or "")
                    gave["time"] = True
                if asked_phone:
                    ph = facts.get("phone") or ""
                    if policy == "with_condition":
                        ph += ", только звоните после 18"
                    if policy == "after_push" and not RX_PHONE_WHY.search(reply) and not phone_pushback_done:
                        pass  # уже обработано выше
                    parts.append(ph)
                    gave["phone"] = True
                if not parts and policy == "refuse" and RX_PHONE.search(reply):
                    parts.append("номер не оставлю, давайте тут спишемся")
            parts = [p for p in parts if p]
            if parts:
                nxt = "\n".join(parts)
            elif "?" in reply and quali:
                nxt = quali.pop(0)      # сперва отвечаем на вопрос бота, инициативы потом
            elif followups:
                nxt = followups.pop(0)
            elif end_intent == "think":
                nxt = THINK[hash(brief["id"]) % len(THINK)]
                end_intent = "_closing"
            elif end_intent == "refuse":
                nxt = REFUSE[hash(brief["id"]) % len(REFUSE)]
                end_intent = "_closing"
            elif end_intent == "silent":
                ended_by = "client_silent"
                break
            elif end_intent == "_closing":
                ended_by = "closed"
                break
            elif "?" in reply:
                nxt = "да"          # запасной ответ на неожиданный вопрос
            else:
                ended_by = "no_more_moves"
                break
        out_msgs.append({"role": "client", "text": nxt})
        msgs.append({"role": "client", "text": nxt})
    else:
        ended_by = ended_by or "cap"

    return {
        "id": brief.get("id"), "title": brief.get("title") or brief.get("id"),
        "type": brief.get("type"), "direction": brief.get("direction"),
        "city": brief.get("city"), "persona": persona,
        "notes": brief.get("notes") or "", "end_intent": brief.get("end_intent"),
        "ended_by": ended_by, "messages": out_msgs,
    }


def main():
    briefs = json.load(open(SRC, encoding="utf-8"))
    if isinstance(briefs, dict):
        briefs = briefs.get("briefs") or []
    if ONLY:
        briefs = [b for b in briefs if b.get("id") in ONLY]
        print("Перегенерация только: %s" % ", ".join(sorted(ONLY)))
    print("Сценариев: %d, потоков: %d" % (len(briefs), WORKERS))
    t0 = time.time()
    results, done = {}, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(play, b, PERSONAS[i % len(PERSONAS)]): b.get("id")
                for i, b in enumerate(briefs)}
        for fu in as_completed(futs):
            bid = futs[fu]
            try:
                results[bid] = fu.result()
            except Exception as e:
                results[bid] = {"id": bid, "title": "ОШИБКА", "messages": [],
                                "ended_by": "crash", "notes": str(e)}
            done += 1
            if done % 10 == 0:
                print("  %d/%d (%.0f сек)" % (done, len(futs), time.time() - t0))
    ordered = [results[b.get("id")] for b in briefs if b.get("id") in results]
    if ONLY and os.path.exists(OUT):
        old = json.load(open(OUT, encoding="utf-8")).get("dialogs", [])
        by_id = {d["id"]: d for d in ordered}
        ordered = [by_id.pop(d["id"], d) for d in old] + list(by_id.values())
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "dialogs": ordered}, f, ensure_ascii=False, indent=1)
    n_bot = sum(1 for d in ordered for m in d["messages"] if m["role"] == "bot")
    print("Готово: %d диалогов, %d ответов бота, %.0f сек -> review_dialogs.json"
          % (len(ordered), n_bot, time.time() - t0))


if __name__ == "__main__":
    main()
