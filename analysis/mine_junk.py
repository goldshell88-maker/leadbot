# -*- coding: utf-8 -*-
"""Майнер КАНДИДАТОВ В МУСОР по всему корпусу Jivo (тролли/приколы/слив времени/оффтоп/
бот-провокации/промпт-инъекции). Широкие эвристики (высокий recall) — точность потом наводит
многоагентный разбор. Плюс КОНТРОЛЬ: короткие ЖИВЫЕ реплики (чтобы не поймать их фильтром) и
реакция оператора-человека (если оператор ответил по делу — значит это НЕ мусор).

Выход:
  junk_candidates.jsonl — по кандидату: {tag, text, key, op_reply, op_engaged, n_client, n_op}
  junk_firstmsgs.txt    — топ уникальных ПЕРВЫХ сообщений клиента (частотность) + редкий хвост
  junk_short.txt        — уникальные КОРОТКИЕ реплики клиента (<=22 симв) с counts
  junk_stats.json       — сводка по тегам
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import paths  # noqa: E402  — единая точка правды по путям (переносимо между машинами)
import sys, io, os, re, glob, json, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SRC = paths.DIALOGS
OUT = paths.ANALYSIS

RE_PHONE = re.compile(r"(?:\+?7|8)?[\s\-\(\)]*\d{3}[\s\-\(\)]*\d{3}[\s\-\(\)]*\d{2}[\s\-\(\)]*\d{2}")
RE_URL = re.compile(r"https?://\S+")


def redact(t):
    t = RE_URL.sub("<ссылка>", t or "")
    t = RE_PHONE.sub("<тел>", t)
    return t


VOWELS = set("аеёиоуыэюяaeiouy")
KEYRUNS = ["qwer", "wert", "erty", "asdf", "sdfg", "dfgh", "zxcv", "xcvb", "cvbn",
           "йцук", "цуке", "укен", "фыва", "ыва", "ячсм", "чсми", "asd", "jkl", "hjkl"]

# ── словари эвристик (широкие, для recall) ────────────────────────────────────
INSULT = re.compile(
    r"\b(?:иди(?:те)?\s+на|пош[её]л\s+ты|пошли\s+вы|нах(?:уй|рен|ер)|ху[ийея]|"
    r"бл[яю]\w*|еба\w*|ёба\w*|пид[оа]р\w*|м(?:у|у)дак\w*|дебил\w*|идиот\w*|"
    r"дур[аиео]\w*|тупо[йе]\w*|кретин\w*|урод\w*|лох\w*|чмо\b|гнида|скотина|"
    r"придурок|долбо|говнюк|сволочь|тварь|мраз[ьи])")
OFFTOP = re.compile(
    r"пицц|доставк\w*\s*(?:еды|пиццы|суши)|суши\b|роллы|такси\b|"
    r"знаком(?:ств|люсь|имся)|свидани|девушк\w*\s*(?:для|познаком)|секс(?!\W*игруш)|"
    r"погод[ауые]|анекдот|расскажи\s*(?:стих|шутк|анекдот|сказк)|стишок|стихотвор|"
    r"поболта|поговорить\s*(?:просто|ни\s*о)|скучно|развлеки|спой|"
    r"президент|путин|навальн|войн[аеуы]|политик|футбол\s*счёт|"
    r"крипт[оы]|биткоин|инвестиц|ставк\w*\s*спорт|казино|"
    r"кредит\s*(?:онлайн|срочно)|займ\s*срочно|порно")
BOTBAIT = re.compile(
    r"ты\s*(?:бот|робот|нейросет|ии\b|исусственн)|"
    r"робот\s*(?:ли|или)|бот\s*(?:ли|или)\s*человек|человек\s*или\s*(?:бот|робот)|"
    r"живой\s*человек|ты\s*живой|это\s*бот|отвечает\s*бот|докажи\s*что\s*не\s*бот|"
    r"c[hн]atgpt|чат\s*гпт|нейронк")
INJECT = re.compile(
    r"игнорир\w*\s*(?:все|инструк|предыдущ)|забудь\s*(?:все\s*)?инструк|"
    r"твои\s*инструкц|систем\w*\s*промпт|system\s*prompt|"
    r"теперь\s*ты\b|представь\s*что\s*ты|веди\s*себя\s*как|act\s*as|"
    r"ignore\s*(?:all|previous|the)|purge|jailbreak|"
    r"повтори\s*за\s*мной|скажи\s*слово|напиши\s*код|напиши\s*программ")
MATH = re.compile(r"сколько\s*буд[ае]т\s*\d|\d\s*[\+\-\*x×]\s*\d|реши\s*пример|"
                  r"квадратн\w*\s*уравнен|интеграл")
TESTWORD = re.compile(r"^(?:тест|test|проверка|проверк\w*\s*связи|ааа+|ааа|тест\w*)$")


def alpha_tokens(t):
    return re.findall(r"[а-яёa-z]+", t.lower())


def is_gibberish(t):
    """Похоже на набор случайных символов / клавиатурный мусор."""
    low = t.lower()
    letters = re.sub(r"[^а-яёa-z]", "", low)
    if len(letters) < 4:
        return False
    # смесь кириллицы и латиницы в одном коротком «слове» без пробелов — часто мусор
    toks = alpha_tokens(low)
    for tok in toks:
        if len(tok) >= 5:
            vr = sum(1 for c in tok if c in VOWELS) / len(tok)
            if vr < 0.16:                       # почти нет гласных → не слово
                return True
            if re.search(r"(.)\1{3,}", tok):    # 4+ одинаковых подряд
                return True
        if any(k in tok for k in KEYRUNS):      # клавиатурный ряд
            return True
    # кир+лат вперемешку внутри токена
    for tok in toks:
        if re.search(r"[а-яё]", tok) and re.search(r"[a-z]", tok) and len(tok) >= 5:
            return True
    return False


def only_emoji_punct(t):
    stripped = re.sub(r"[\w\s]", "", t, flags=re.UNICODE)      # убрать буквы/цифры/пробелы
    letters = re.sub(r"[^\w]", "", t, flags=re.UNICODE)
    return len(letters) == 0 and len(t.strip()) > 0 and len(stripped) >= 0


def tag_message(t):
    """Вернёт тег-кандидат мусора или None. Порядок = приоритет."""
    raw = (t or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if only_emoji_punct(raw) and not re.search(r"[а-яёa-z0-9]", low):
        return "emoji_punct_only"
    if INJECT.search(low):
        return "prompt_inject"
    if BOTBAIT.search(low):
        return "bot_bait"
    if INSULT.search(low):
        return "insult"
    if OFFTOP.search(low):
        return "offtopic"
    if MATH.search(low):
        return "math_test"
    if TESTWORD.match(low):
        return "test_word"
    if is_gibberish(raw):
        return "gibberish"
    return None


files = sorted(glob.glob(os.path.join(SRC, "*.json")))
print("Диалогов в корпусе:", len(files))

candidates = []
first_msgs = collections.Counter()
short_msgs = collections.Counter()
tag_counts = collections.Counter()
seen_cand = set()
total_client = 0

for fp in files:
    try:
        d = json.load(open(fp, encoding="utf-8"))
    except Exception:
        continue
    msgs = d.get("messages", []) or []
    cl = [m for m in msgs if m.get("role") == "client" and (m.get("text") or "").strip()]
    op = [m for m in msgs if m.get("role") == "operator" and (m.get("text") or "").strip()]
    key = d.get("conversation_key")
    if cl:
        fm = redact(cl[0].get("text", "")).strip()
        if fm and "<ФОТО>" not in fm and "<фото>" not in fm.lower():
            first_msgs[fm[:90]] += 1
    for m in cl:
        total_client += 1
        txt = redact(m.get("text", "")).strip()
        if not txt or "ФОТО" in txt.upper():
            continue
        # короткие реплики (контроль живых + мусор)
        norm = re.sub(r"\s+", " ", txt).strip()
        if len(norm) <= 22:
            short_msgs[norm.lower()] += 1
        tag = tag_message(txt)
        if tag:
            tag_counts[tag] += 1
            k2 = (tag, norm.lower()[:60])
            if k2 in seen_cand:
                continue
            seen_cand.add(k2)
            op_reply = redact(op[0].get("text", "")).strip()[:120] if op else ""
            # оператор «вовлёкся» = ответил осмысленно (не пусто, длиннее приветствия)
            op_engaged = bool(op) and len(re.sub(r"\s+", "", op_reply)) > 12
            candidates.append({
                "tag": tag, "text": txt[:200], "key": key,
                "op_reply": op_reply, "op_engaged": op_engaged,
                "n_client": len(cl), "n_op": len(op),
            })

# сортируем кандидатов по тегу для удобства чтения
candidates.sort(key=lambda x: (x["tag"], -x["n_op"]))
with open(os.path.join(OUT, "junk_candidates.jsonl"), "w", encoding="utf-8") as f:
    for c in candidates:
        f.write(json.dumps(c, ensure_ascii=False) + "\n")

with open(os.path.join(OUT, "junk_firstmsgs.txt"), "w", encoding="utf-8") as f:
    f.write("# ТОП-250 первых сообщений клиента (частотность) — верх=шаблонные живые\n")
    for t, c in first_msgs.most_common(250):
        f.write("%4d  %s\n" % (c, t))
    f.write("\n# ХВОСТ: 200 РЕДКИХ первых сообщений (встречались 1 раз) — тут прячется мусор\n")
    rare = [t for t, c in first_msgs.items() if c == 1]
    for t in rare[:200]:
        f.write("   1  %s\n" % t)

with open(os.path.join(OUT, "junk_short.txt"), "w", encoding="utf-8") as f:
    f.write("# Уникальные КОРОТКИЕ реплики клиента (<=22 симв) с counts — контроль живых терсовых\n")
    for t, c in short_msgs.most_common(400):
        f.write("%4d  %s\n" % (c, t))

json.dump({
    "dialogs": len(files), "total_client_msgs": total_client,
    "candidates": len(candidates), "unique_candidates": len(seen_cand),
    "by_tag": dict(tag_counts.most_common()),
}, open(os.path.join(OUT, "junk_stats.json"), "w", encoding="utf-8"),
    ensure_ascii=False, indent=1)

print("\nКандидатов в мусор (уник.):", len(seen_cand), "из", total_client, "реплик клиента")
print("По тегам:", dict(tag_counts.most_common()))
print("Файлы: junk_candidates.jsonl, junk_firstmsgs.txt, junk_short.txt, junk_stats.json")
