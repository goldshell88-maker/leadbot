# -*- coding: utf-8 -*-
"""СТАТИСТИКА ОПЕРАТОРСКИХ ДИАЛОГОВ для ТЗ «эталон-50» (03.08.2026).

Два корпуса:
  1) merged — приёмник Jivo, C:\\Work\\Jivo Webhook\\data\\dialogs_merged\\*.json
     (мета объявления, visitor.name, role client/agent);
  2) kp — выгрузка прошлого подрядчика, all-dialogs-grouped.jsonl.gz (только messages).

Считает по каждому корпусу: реплик/ходов до записи (среднее, медиана), % записей,
полноту адрес+телефон, конкретное время vs «когда удобно», вопрос модели, финальную
фразу, стиль (слова/реплику, приветствия, имя, эмодзи, тире, «!»), вопросы клиентов.
Пишет:  analysis/_op_stats.json  +  analysis/_op_stats.txt (читабельный отчёт)
        + выборки для качественного разбора в --samples-dir (по N на страту).

Запуск:  py analysis\\op_stats.py [--samples-dir DIR] [--per-stratum 60]
"""
import sys, io, os, re, json, gzip, glob, random, statistics as st

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
MERGED_DIR = r"C:\Work\Jivo Webhook\data\dialogs_merged"
KP_GZ = r"C:\Work\КПППППП\jivo_all_dialogs_2026-07-30\all-dialogs-grouped.jsonl.gz"

SAMPLES_DIR = None
PER_STRATUM = 60
args = sys.argv[1:]
if "--samples-dir" in args:
    SAMPLES_DIR = args[args.index("--samples-dir") + 1]
if "--per-stratum" in args:
    PER_STRATUM = int(args[args.index("--per-stratum") + 1])

RX_BOOK = re.compile(r"запис(?:ал|ыва|ан)|оформ(?:ил|ляю)\s+заяв", re.I)
RX_PHONE = re.compile(r"(?:\+?7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}")
RX_ADDR = re.compile(r"(?:\bул\.?\s|улиц|просп|пр-?т|переул|\bпер\.\s|бульвар|\bб-р|шоссе|"
                     r"мкр|микрорайон|проезд|набережн|\bдом\s?\d|\bд\.\s?\d|\bкв\.?\s?\d)", re.I)
RX_TIME_CONCRETE = re.compile(r"(?:\b[кв]\s|до\s)\d{1,2}(?:[:.]\d{2})?\b|через\s+(?:час|полчаса|минут|\d)|"
                              r"в\s+течени[ие]\s+(?:час|получас|\d)|час[аоу]?[\s-]полтора|полтора\s+часа|"
                              r"два\s+с\s+половиной|завтра\s+(?:утром|днём|днем|вечером|к\s|с\s\d)|"
                              r"сегодня\s+(?:до|к|после)\s", re.I)
RX_WHEN_OPEN = re.compile(r"когда\s+(?:вам\s+)?(?:будет\s+)?удобн|когда\s+(?:вы\s+)?(?:с)?можете|"
                          r"во\s+сколько\s+вам|в\s+какое\s+время\s+(?:вам\s+)?удобн", re.I)
RX_PHONE_ASK = re.compile(r"(?:номер|телефон)\w*\s*(?:телефона)?\s*(?:подскаж|оставьте|напишите|продиктуйте|"
                          r"скажите|можно|для\s+связи)|(?:подскажите|оставьте|напишите).{0,25}(?:номер|телефон)|"
                          r"(?:номер|телефон)[\w\s]{0,15}\?", re.I)
RX_ADDR_ASK = re.compile(r"адрес", re.I)
RX_MODEL_ASK = re.compile(r"марк[ауие]|модель|модели", re.I)
RX_FINAL = re.compile(r"не\s+трогайте|не\s+включайте|сами\s+не\s+(?:чините|разбирайте|ремонтируйте|трогайте)|"
                      r"не\s+разбирайте|не\s+пытайтесь\s+сами", re.I)
RX_GREET = re.compile(r"здравствуй|добрый\s+(?:день|вечер|утро)|доброго\s+(?:дня|вечера|утра|времени)|привет", re.I)
RX_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
RX_DASH = re.compile(r"—|–|\s-\s")
RX_Q_PRICE = re.compile(r"сколько\s+(?:сто[ий]|будет|возьм|обойд|встан)|стоимост|цен[аыу]\b|поч[её]м|прайс|расценк", re.I)
RX_Q_TIME = re.compile(r"когда\s+(?:с)?можете|когда\s+приед|во\s+сколько|как\s+быстро|сегодня\s+(?:с)?можете|"
                       r"сколько\s+ждать|как\s+скоро", re.I)
RX_Q_TECH = re.compile(r"почему|из-за\s+чего|что\s+(?:это\s+)?может\s+быть|в\s+ч[ёе]м\s+(?:может\s+быть\s+)?"
                       r"(?:причина|дело)|сложн[оа]\s+ли|можно\s+ли\s+(?:почин|отремонт|заменить)", re.I)
RX_OBJECT = re.compile(r"дорого|дешевле|скидк|подумаю|сам\s+сдела|сам\s+почин|друг(?:ие|ой)\s+(?:мастер|предлож|"
                       r"фирм)|почему\s+так\s+(?:дорого|много)|за\s+что\s+так", re.I)
RX_NBRAND = re.compile(r"bosch|samsung|\blg\b|indesit|ariston|electrolux|атлант|бирюса|candy|beko|haier|midea|"
                       r"gorenje|whirlpool|zanussi|\bhp\b|asus|acer|lenovo|dell|msi|huawei|индезит|бош|самсунг|"
                       r"аристон|электролюкс|леново|асус", re.I)

WORD_RX = re.compile(r"[а-яёa-z0-9]+", re.I)


def norm_turns(messages):
    """Склеить подряд идущие сообщения одной роли в ходы. [(role, text, ts), ...]"""
    turns = []
    for m in messages:
        role = m.get("role") or ""
        if role not in ("client", "agent"):
            t = (m.get("type") or "").lower()
            role = "client" if t == "visitor" else ("agent" if t == "agent" else "")
        if not role:
            continue
        text = (m.get("text") or "").strip()
        if not text:
            continue
        if turns and turns[-1][0] == role:
            turns[-1] = (role, turns[-1][1] + "\n" + text, turns[-1][2])
        else:
            turns.append((role, text, m.get("ts") or m.get("time") or ""))
    return turns


def iter_merged():
    for fp in sorted(glob.glob(os.path.join(MERGED_DIR, "*.json"))):
        try:
            with open(fp, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        meta = d.get("meta") or {}
        page = (meta.get("page") or {}).get("title") or ""
        vis = (meta.get("visitor") or {}).get("name") or ""
        yield {"id": os.path.basename(fp), "ad": page, "client_name": vis,
               "msgs": d.get("messages") or []}


def iter_kp():
    with gzip.open(KP_GZ, "rt", encoding="utf-8") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            yield {"id": "kp_%06d" % i, "ad": "", "client_name": "",
                   "msgs": d.get("messages") or []}


def first_name(full):
    full = (full or "").strip()
    if not full:
        return ""
    tok = full.split()[0]
    return tok if re.fullmatch(r"[А-ЯЁ][а-яё]{2,}", tok) else ""


def analyze(dialogs, use_names):
    D = dict(total=0, with_agent=0, booked=0,
             turns_to_book=[], agent_msgs_to_book=[], minutes_to_book=[],
             booked_phone=0, booked_addr=0, booked_both=0,
             time_concrete=0, when_open=0, phone_ask=0, addr_ask=0, model_ask=0,
             final_phrase=0, greet_first=0, name_used=0, name_known=0,
             agent_words=[], agent_msg_count=[],
             am_emoji=0, am_dash=0, am_excl=0, am_total=0,
             q_price=0, q_time=0, q_tech=0, q_object=0, client_brand=0,
             samples={"booked": [], "unbooked": [], "price": [], "objection": []})
    rnd = random.Random(44)
    for d in dialogs:
        turns = norm_turns(d["msgs"])
        if not turns:
            continue
        D["total"] += 1
        agent_turns = [t for t in turns if t[0] == "agent"]
        client_turns = [t for t in turns if t[0] == "client"]
        if not agent_turns:
            continue
        D["with_agent"] += 1
        agent_all = "\n".join(t[1] for t in agent_turns)
        client_all = "\n".join(t[1] for t in client_turns)

        book_idx = None
        for i, (role, text, _) in enumerate(turns):
            if role == "agent" and RX_BOOK.search(text):
                book_idx = i
                break
        booked = book_idx is not None
        if booked:
            D["booked"] += 1
            D["turns_to_book"].append(book_idx + 1)
            D["agent_msgs_to_book"].append(sum(1 for t in turns[:book_idx + 1] if t[0] == "agent"))
            has_p = bool(RX_PHONE.search(client_all))
            has_a = bool(RX_ADDR.search(client_all))
            D["booked_phone"] += has_p
            D["booked_addr"] += has_a
            D["booked_both"] += (has_p and has_a)

        D["time_concrete"] += bool(RX_TIME_CONCRETE.search(agent_all))
        D["when_open"] += bool(RX_WHEN_OPEN.search(agent_all))
        D["phone_ask"] += bool(RX_PHONE_ASK.search(agent_all))
        D["addr_ask"] += bool(RX_ADDR_ASK.search(agent_all))
        D["model_ask"] += bool(RX_MODEL_ASK.search(agent_all))
        D["final_phrase"] += bool(RX_FINAL.search(agent_all))
        D["greet_first"] += bool(RX_GREET.search(agent_turns[0][1]))
        if use_names:
            nm = first_name(d["client_name"])
            if nm:
                D["name_known"] += 1
                D["name_used"] += bool(re.search(re.escape(nm), agent_all))
        D["agent_msg_count"].append(len(agent_turns))
        for _, text, _ in agent_turns:
            D["am_total"] += 1
            D["agent_words"].append(len(WORD_RX.findall(text)))
            D["am_emoji"] += bool(RX_EMOJI.search(text))
            D["am_dash"] += bool(RX_DASH.search(text))
            D["am_excl"] += ("!" in text)

        q_price = bool(RX_Q_PRICE.search(client_all))
        q_obj = bool(RX_OBJECT.search(client_all))
        D["q_price"] += q_price
        D["q_time"] += bool(RX_Q_TIME.search(client_all))
        D["q_tech"] += bool(RX_Q_TECH.search(client_all))
        D["q_object"] += q_obj
        D["client_brand"] += bool(RX_NBRAND.search(client_all))

        # выборки для качественного разбора (резервуарное сэмплирование)
        def put(strat):
            bucket = D["samples"][strat]
            item = (d["id"], d["ad"], d["client_name"], turns)
            if len(bucket) < PER_STRATUM:
                bucket.append(item)
            else:
                j = rnd.randrange(0, D["total"])
                if j < PER_STRATUM:
                    bucket[j % PER_STRATUM] = item
        put("booked" if booked else "unbooked")
        if q_price:
            put("price")
        if q_obj:
            put("objection")
    return D


def pct(n, d):
    return "%.1f%%" % (100.0 * n / d) if d else "n/a"


def report(tag, D, out):
    wa = D["with_agent"]
    out.append("=== КОРПУС %s ===" % tag)
    out.append("диалогов всего: %d, с ответом оператора: %d" % (D["total"], wa))
    out.append("дошло до записи (по маркеру «запис…» у оператора): %d (%s от диалогов с оператором)"
               % (D["booked"], pct(D["booked"], wa)))
    if D["turns_to_book"]:
        out.append("ходов (клиент+оператор, склеенных) до записи: среднее %.1f, медиана %d"
                   % (st.mean(D["turns_to_book"]), st.median(D["turns_to_book"])))
        out.append("реплик оператора до записи: среднее %.1f, медиана %d"
                   % (st.mean(D["agent_msgs_to_book"]), st.median(D["agent_msgs_to_book"])))
        out.append("полнота данных в записанных: телефон %s, адрес %s, оба %s"
                   % (pct(D["booked_phone"], D["booked"]), pct(D["booked_addr"], D["booked"]),
                      pct(D["booked_both"], D["booked"])))
    out.append("оператор предлагал КОНКРЕТНОЕ время: %s диалогов; открытый «когда удобно»: %s"
               % (pct(D["time_concrete"], wa), pct(D["when_open"], wa)))
    out.append("спросил телефон: %s, адрес: %s, марку/модель: %s"
               % (pct(D["phone_ask"], wa), pct(D["addr_ask"], wa), pct(D["model_ask"], wa)))
    out.append("финальная фраза («сами не трогайте/не чините»): %s" % pct(D["final_phrase"], wa))
    out.append("приветствие в ПЕРВОЙ реплике оператора: %s" % pct(D["greet_first"], wa))
    if D["name_known"]:
        out.append("обращение по имени (имя известно из меты): %s (%d диалогов с именем)"
                   % (pct(D["name_used"], D["name_known"]), D["name_known"]))
    if D["agent_words"]:
        out.append("слов в реплике оператора: среднее %.1f, медиана %d (реплик: %d)"
                   % (st.mean(D["agent_words"]), st.median(D["agent_words"]), D["am_total"]))
        out.append("реплик оператора за диалог: среднее %.1f, медиана %d"
                   % (st.mean(D["agent_msg_count"]), st.median(D["agent_msg_count"])))
    out.append("стиль реплик оператора: эмодзи %s, тире %s, «!» %s"
               % (pct(D["am_emoji"], D["am_total"]), pct(D["am_dash"], D["am_total"]),
                  pct(D["am_excl"], D["am_total"])))
    out.append("клиенты спрашивали: цену %s, время/скорость %s, технические «почему/можно ли» %s; "
               "возражения %s; клиент сам назвал марку %s"
               % (pct(D["q_price"], wa), pct(D["q_time"], wa), pct(D["q_tech"], wa),
                  pct(D["q_object"], wa), pct(D["client_brand"], wa)))
    out.append("")


def dump_samples(tag, D):
    if not SAMPLES_DIR:
        return
    for strat, items in D["samples"].items():
        sd = os.path.join(SAMPLES_DIR, tag, strat)
        os.makedirs(sd, exist_ok=True)
        for did, ad, nm, turns in items:
            fp = os.path.join(sd, re.sub(r"[^\w.-]", "_", did) + ".txt")
            with open(fp, "w", encoding="utf-8") as f:
                if ad:
                    f.write("ОБЪЯВЛЕНИЕ: %s\n" % ad)
                if nm:
                    f.write("КЛИЕНТ: %s\n" % nm)
                f.write("-" * 60 + "\n")
                for role, text, ts in turns:
                    who = "К" if role == "client" else "О"
                    f.write("%s: %s\n" % (who, text.replace("\n", "\n   ")))


def main():
    out = []
    res = {}
    for tag, it, names in (("merged (наши операторы, 24.07-02.08)", iter_merged(), True),
                           ("КПППППП (прошлый подрядчик)", iter_kp(), False)):
        D = analyze(it, names)
        report(tag, D, out)
        dump_samples(tag.split()[0], D)
        res[tag] = {k: v for k, v in D.items() if k not in ("samples", "agent_words")}
    txt = "\n".join(out)
    print(txt)
    with open(os.path.join(HERE, "_op_stats.txt"), "w", encoding="utf-8") as f:
        f.write(txt + "\n")
    with open(os.path.join(HERE, "_op_stats.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
