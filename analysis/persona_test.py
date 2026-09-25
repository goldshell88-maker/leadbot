# -*- coding: utf-8 -*-
"""Адверсариальный стенд: гоняет бота против «трудных» клиентов.

Клиент имитируется отдельным вызовом Claude по персоне. Диалог идёт до записи
(bot ready), до отказа клиента или лимита ходов. Транскрипты -> persona_tests/.
Запускать ПОСЛЕ обновления brain/prompt.py — стенд подхватит новый мозг.
"""
import paths  # noqa: E402  — единая точка правды по путям
import sys, io, os, json, importlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import claude_api
import server  # использует prompt.py

OUT = os.path.join(paths.ANALYSIS, "persona_tests")
os.makedirs(OUT, exist_ok=True)

PERSONAS = [
  ("холодильник_цена_отказ",
   "Ты — клиент с Avito. У тебя перестал морозить холодильник. Пиши коротко, по-человечески, по одной реплике. "
   "Сначала спроси, приедут ли посмотреть. Потом ОБЯЗАТЕЛЬНО дожимай по цене ('а сколько стоит?', 'хотя бы примерно'). "
   "Если мастер сыпет требованиями адреса/телефона в лоб и не снимает твоё беспокойство — насторожись ('вы как-то быстро пишете') и скажи 'спасибо, не нужно'. "
   "НО если мастер общается тепло, как живой человек, обещает бесплатный выезд и не давит — ты смягчаешься и в итоге даёшь адрес (Москва, Ленина 10, кв 5) и телефон 89261112233, соглашаешься на завтра днём."),

  ("мебель_торг_точная_цена",
   "Ты — клиент, нужно собрать кухню и повесить 20 гардин. Настойчиво требуешь ТОЧНУЮ цену за штуку и за всё, торгуешься, "
   "говоришь 'не хочу тратить время зря'. Если мастер грубит или обесценивает — уходишь. Если держит линию вежливо, обещает бесплатный выезд "
   "и объясняет, что цена за фронт работ — соглашаешься дать адрес (Спб, Невский 100) и телефон 89031234567 на выходные."),

  ("недоверчивый_скептик",
   "Ты — недоверчивый клиент. 'Все вы одинаковые, приедете и раскрутите на деньги'. Проверяешь мастера каверзными вопросами. "
   "Сдаёшься и даёшь контакт (Казань, Баумана 5, тел 89170001122) ТОЛЬКО если мастер отвечает уверенно, по-человечески, без корпоративных штампов."),

  ("не_наше_направление_кп",
   "Ты — клиент: 'нужно настроить сервер и 1С Бухгалтерию в офисе на 10 компьютеров'. Это сложная корпоративная задача. "
   "Смотри, как мастер поступит: должен сказать, что он или коллега перезвонит, и взять контакт, а не обещать сам всё решить."),

  ("тёплый_готовый",
   "Ты — тёплый готовый клиент: стиральная машина не сливает, сразу готов записаться. Не капризничаешь. "
   "Если мастер нормальный — быстро даёшь адрес (Ростов, Мира 15, кв 3) и телефон 89281234567, соглашаешься на сегодня вечер."),
]

def client_next(persona_sys, transcript):
    """Следующая реплика клиента. Реплики мастера -> user, клиента -> assistant."""
    msgs = []
    for who, text in transcript:
        role = "user" if who == "bot" else "assistant"
        msgs.append({"role": role, "content": text})
    if not msgs:
        msgs = [{"role": "user", "content": "(клиент открывает чат первым — напиши первое сообщение)"}]
    if msgs and msgs[0]["role"] == "assistant":
        msgs.insert(0, {"role": "user", "content": "(начни диалог)"})
    resp = claude_api.messages(system=persona_sys, msgs=msgs, model="claude-sonnet-5", max_tokens=300)
    return claude_api.first_text(resp) or "(клиент молчит)"

END_MARKERS = ("не нужно", "не надо", "всего доброго", "до свидания", "передумал", "ищу дальше")

def run_persona(name, persona_sys, max_turns=9):
    importlib.reload(server)  # свежий prompt.py
    did = "persona_" + name
    transcript = []
    result = {"name": name, "closed": False, "state": None, "turns": 0}
    for i in range(max_turns):
        cmsg = client_next(persona_sys, transcript)
        transcript.append(("client", cmsg))
        if any(m in cmsg.lower() for m in END_MARKERS) and i >= 2:
            # клиент завершает — дадим боту один шанс удержать, потом стоп
            r = server.run_turn(did, cmsg)
            transcript.append(("bot", r["reply"]))
            result["turns"] = i + 1
            result["state"] = r["state"]; result["closed"] = r["ready"]
            break
        r = server.run_turn(did, cmsg)
        transcript.append(("bot", r["reply"]))
        result["turns"] = i + 1
        result["state"] = r["state"]
        if r["ready"]:
            result["closed"] = True
            break
    # сохранить
    lines = ["ПЕРСОНА: %s | закрыто=%s | ходов=%d" % (name, result["closed"], result["turns"]), ""]
    for who, text in transcript:
        lines.append(("КЛИЕНТ  | " if who == "client" else "МАСТЕР  | ") + text.replace("\n", " ⏎ "))
    open(os.path.join(OUT, name + ".txt"), "w", encoding="utf-8").write("\n".join(lines))
    return result, transcript

if __name__ == "__main__":
    print("Гоняю персоны через обновлённого бота...\n")
    summary = []
    for name, psys in PERSONAS:
        res, tr = run_persona(name, psys)
        summary.append(res)
        print("=" * 70)
        print("ПЕРСОНА: %s  ->  закрыто=%s, ходов=%d" % (name, res["closed"], res["turns"]))
        for who, text in tr:
            tag = "КЛИЕНТ " if who == "client" else "МАСТЕР "
            print("  %s| %s" % (tag, text[:150].replace("\n", " ")))
        st = res["state"] or {}
        print("  собрано: тел=%r адрес=%r время=%r кат=%s" %
              (st.get("phone"), st.get("address"), st.get("preferred_time"), st.get("category")))
    closed = sum(1 for s in summary if s["closed"])
    print("\n" + "=" * 70)
    print("ИТОГ: закрыто %d из %d персон" % (closed, len(summary)))
    json.dump(summary, open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
