# -*- coding: utf-8 -*-
"""СТИЛЬ ОПЕРАТОРОВ: начала реплик (акки), закрывающие вопросы, эхо проблемы.
По полному merged-корпусу. 0 токенов. Результат — mine_style_stats.json + консоль."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import paths  # noqa: E402  — единая точка правды по путям (переносимо между машинами)
import sys, io, os, re, glob, json, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SRC = paths.MERGED
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mine_style_stats.json")

RE_PHONE = re.compile(r"(?:\+?7|8)?[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}")
RE_URL = re.compile(r"https?://\S+")
ACKS = {"понял", "понятно", "хорошо", "ок", "окей", "да", "ясно", "отлично", "принял",
        "договорились", "спасибо", "ага", "угу", "супер", "ладно"}
GREET = {"здравствуйте", "добрый", "доброго", "привет", "приветствую", "доброе"}

RX_TIME_Q = re.compile(r"когда|во сколько|какое время|сегодня|завтра|удобно", re.I)
RX_ADDR_Q = re.compile(r"адрес|куда|territор|территориальн|город|проживаете", re.I)
RX_PHONE_Q = re.compile(r"номер|телефон", re.I)


def norm_words(t):
    return re.findall(r"[а-яёa-z0-9]+", (t or "").lower())


def first_word(t):
    w = norm_words(t)
    return w[0] if w else ""


def has_echo(op_text, client_text, n=4):
    """Есть ли в реплике оператора непрерывная цепочка из n слов из реплики клиента."""
    ow, cw = norm_words(op_text), norm_words(client_text)
    if len(cw) < n or len(ow) < n:
        return False
    cset = {" ".join(cw[i:i + n]) for i in range(len(cw) - n + 1)}
    for i in range(len(ow) - n + 1):
        if " ".join(ow[i:i + n]) in cset:
            return True
    return False


def classify_prev(t):
    tl = (t or "").lower()
    if RE_PHONE.search(tl) or re.search(r"\bул\b|улиц|кв\.?\s?\d|подъезд|этаж", tl):
        return "данные"
    if re.search(r"^\s*(да|давайте|хорошо|ок|окей|договорились|согласен|согласна|можно)\b", tl):
        return "согласие"
    if "?" in tl:
        return "вопрос"
    return "прочее"


def main():
    openers = collections.Counter()          # первое слово реплики оператора (не приветствие)
    ack_by_prev = collections.Counter()      # (тип предыдущей реплики клиента, старт-акк?)
    time_qs = collections.Counter()          # формулировки вопросов о времени
    repeat_same_start = 0                    # две подряд реплики оператора с одним первым словом
    total_pairs = 0
    echo_ops = 0
    total_ops_after_client = 0
    n_msgs = 0
    for fp in glob.glob(os.path.join(SRC, "*.json")):
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        msgs = [m for m in (d.get("messages") or []) if (m.get("text") or "").strip()]
        prev_op_first = None
        for i, m in enumerate(msgs):
            if m.get("role") != "operator":
                continue
            t = RE_URL.sub(" ", m.get("text") or "")
            n_msgs += 1
            fw = first_word(t)
            if fw and fw not in GREET:
                openers[fw] += 1
                if prev_op_first is not None:
                    total_pairs += 1
                    if fw == prev_op_first and fw in ACKS:
                        repeat_same_start += 1
                prev_op_first = fw
            # контекст акка
            if i > 0 and msgs[i - 1].get("role") == "client":
                prev_t = msgs[i - 1].get("text") or ""
                ack_by_prev[(classify_prev(prev_t), fw in ACKS)] += 1
                total_ops_after_client += 1
                if has_echo(t, prev_t):
                    echo_ops += 1
            # вопросы о времени: последний вопрос реплики
            for q in re.findall(r"[^.!?\n]*\?", t):
                qn = re.sub(r"\s+", " ", q.strip().lower())
                if RX_TIME_Q.search(qn) and not RX_ADDR_Q.search(qn) and not RX_PHONE_Q.search(qn) \
                        and 8 <= len(qn) <= 70:
                    time_qs[qn] += 1
    res = {
        "operator_msgs": n_msgs,
        "openers_top60": openers.most_common(60),
        "ack_rate_by_prev": {"%s|ack=%s" % k: v for k, v in ack_by_prev.items()},
        "same_ack_twice_in_row": repeat_same_start, "op_reply_pairs": total_pairs,
        "echo_ops_4words": echo_ops, "ops_after_client": total_ops_after_client,
        "time_questions_top40": time_qs.most_common(40),
    }
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("Реплик операторов: %d" % n_msgs)
    print("\nТОП-30 первых слов (без приветствий):")
    for w, c in openers.most_common(30):
        print("  %5d  %s" % (c, w))
    print("\nАкк-старт по типу предыдущей реплики клиента:")
    agg = collections.defaultdict(lambda: [0, 0])
    for (prev, is_ack), c in ack_by_prev.items():
        agg[prev][0 if is_ack else 1] += c
    for prev, (a, na) in agg.items():
        print("  %-9s акк %4d / не-акк %5d  (%.0f%%)" % (prev, a, na, 100.0 * a / max(1, a + na)))
    print("\nОдинаковый акк две реплики подряд: %d из %d пар (%.1f%%)"
          % (repeat_same_start, total_pairs, 100.0 * repeat_same_start / max(1, total_pairs)))
    print("ЭХО (4+ слов клиента подряд): %d из %d (%.1f%%)"
          % (echo_ops, total_ops_after_client, 100.0 * echo_ops / max(1, total_ops_after_client)))
    print("\nТОП-25 вопросов о времени:")
    for q, c in time_qs.most_common(25):
        print("  %4d  %s" % (c, q))


if __name__ == "__main__":
    main()
