# -*- coding: utf-8 -*-
"""Типология ВСЕХ диалогов: раскладывает каждый по типу + считает развитие/исход.
Даёт распределение и обезличенные выборки по типам (для разбора потоков и точек handoff)."""
import paths  # noqa: E402  — единая точка правды по путям
import sys, io, os, re, glob, json, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import prefilter  # noqa

SRC = paths.DIALOGS
OUT = paths.ANALYSIS

RE_PHONE = re.compile(r"(?:\+?7|8)?[\s\-\(\)]*\d{3}[\s\-\(\)]*\d{3}[\s\-\(\)]*\d{2}[\s\-\(\)]*\d{2}")
RE_URL = re.compile(r"https?://\S+")
RX_MSGR = re.compile(r"ватсап|вотсап|вацап|whats|телеграм|вайбер|\bтг\b|\bмах\b|\bмакс\b", re.I)
RX_PRICE = re.compile(r"скольк|\bцен|стоимост|\bстоит|поч[её]м|прайс|сумм|сориентир", re.I)
RX_GEO = re.compile(r"за\s*город|снт|деревн|посёл|посел|\bсел[оа]\b|райо?н\b|область|пригород|"
                    r"выезжаете\s+в|вы\s+в\s+[а-я]", re.I)
RX_CORP = re.compile(r"юрлиц|юр\.?\s*лиц|организац|компани|офис|предприят|гостиниц|отел|"
                     r"безнал|по\s+договор|счёт|счет-фактур|несколько\s+(?:рабочих\s+)?мест|сервер", re.I)
RX_JOB = re.compile(r"собра|повес|устано|подключ|почин|настро|помен|замен|отремонт|сдела|"
                    r"навес|прикру|прочист|устран|разоб|перевес|закреп|врез|запра|прошив|обжа|"
                    r"не\s+работает|не\s+включ|слома|течёт|течет|засор|сгорел|не\s+сливает|не\s+морозит", re.I)
RX_TROLL = re.compile(r"дурак|идиот|мудак|クso|говно|хрень|бесполезн|некомпетент|"
                      r"беспомощн|криворук", re.I)
RX_CLOSER = re.compile(r"^\s*(спасибо|хорошо|ок|окей|понял|поняла|договорил|до встречи|до завтра|"
                       r"благодар|👍|🤝|ясно|нет,?\s*спасибо)\W*$", re.I)


def redact(t):
    return RE_PHONE.sub("<тел>", RE_URL.sub("<ссылка>", t or ""))


files = sorted(glob.glob(os.path.join(SRC, "*.json")))
types = collections.Counter()
outcome = collections.Counter()
samples = collections.defaultdict(list)
turns_by_type = collections.defaultdict(list)


def classify(client_text, first_text, n_msgs, has_photo, phone_ok, n_op):
    ct = client_text.lower()
    if prefilter.check_complaint(ct):
        return "жалоба/претензия"
    if prefilter.check_refuse(ct):
        return "отказ (не наш профиль)"
    if RX_TROLL.search(ct):
        return "троллинг/провокация"
    if RX_CORP.search(ct):
        return "корпоратив/юрлицо"
    if prefilter.check_soglasovanie(ct):
        return "по согласованию"
    if RX_MSGR.search(ct):
        return "увод в мессенджер"
    if RX_GEO.search(ct):
        return "география/за-город"
    # закрывашки: только короткие благодарности/подтверждения, работы не описано
    if n_op > 0 and not RX_JOB.search(ct) and all(RX_CLOSER.match(x) for x in [first_text] if x):
        pass
    # содержательные типы
    job = bool(RX_JOB.search(ct))
    price = bool(RX_PRICE.search(ct))
    if not job and not price and len(RE_URL.sub("", ct).strip()) < 12 and not has_photo:
        return "неясный/нестандарт (нет задачи)"
    if has_photo and not job:
        return "фото-диагностика"
    if job and price:
        return "заявка + вопрос цены"
    if job:
        return "чёткая заявка"
    if price:
        return "ценовой шоппер"
    return "прочее/неясное"


for fp in files:
    try:
        d = json.load(open(fp, encoding="utf-8"))
    except Exception:
        continue
    msgs = d.get("messages", []) or []
    cl = [m for m in msgs if m.get("role") == "client"]
    op = [m for m in msgs if m.get("role") == "operator"]
    if not cl:
        continue
    client_text = "\n".join(m.get("text", "") for m in cl)
    has_photo = "🖼" in client_text
    phone_ok = bool(RE_PHONE.search(client_text) or (d.get("meta", {}).get("visitor", {}) or {}).get("phone"))
    t = classify(client_text, (cl[0].get("text") or "").strip(), len(msgs), has_photo, phone_ok, len(op))
    types[t] += 1
    turns_by_type[t].append(len(msgs))
    # исход
    if phone_ok:
        outcome[(t, "телефон собран")] += 1
    elif len(op) == 0:
        outcome[(t, "оператор не ответил")] += 1
    elif msgs and msgs[-1].get("role") == "client":
        outcome[(t, "оборвался на клиенте")] += 1
    else:
        outcome[(t, "оборвался/без телефона")] += 1
    if len(samples[t]) < 30 and len(op) > 0 and 3 <= len(msgs) <= 40:
        samples[t].append({
            "key": d.get("conversation_key"),
            "title": ((d.get("meta") or {}).get("page") or {}).get("title", "")[:55],
            "phone": phone_ok,
            "m": [{"r": ("К" if m.get("role") == "client" else "О"), "t": redact(m.get("text", ""))}
                  for m in msgs if m.get("text")],
        })

tot = sum(types.values())
print("=" * 60)
print("ТИПОЛОГИЯ ДИАЛОГОВ  (всего %d)" % tot)
print("=" * 60)
for t, c in types.most_common():
    tt = turns_by_type[t]
    med = sorted(tt)[len(tt) // 2] if tt else 0
    ph = outcome.get((t, "телефон собран"), 0)
    print("%-32s %4d (%2.0f%%)  медиана ходов %2d  телефон %d (%2.0f%%)" % (
        t, c, 100 * c / tot, med, ph, 100 * ph / c if c else 0))

json.dump({"types": dict(types), "outcome": {f"{a}||{b}": v for (a, b), v in outcome.items()}},
          open(os.path.join(OUT, "typology.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
with open(os.path.join(OUT, "typology_samples.jsonl"), "w", encoding="utf-8") as f:
    for t, arr in samples.items():
        for s in arr[:12]:               # до 12 примеров на тип для разбора потоков
            s["type"] = t
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
print("\nФайлы: typology.json, typology_samples.jsonl")
