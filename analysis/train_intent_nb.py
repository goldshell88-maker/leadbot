# -*- coding: utf-8 -*-
"""ОБУЧЕНИЕ собственной NLU-модели (наивный Байес) на классах RAG-пула: тип клиентской
реплики → класс. Чистый stdlib. Выход: brain/intent_nb.json (логвероятности).
Запуск:  py analysis\\train_intent_nb.py"""
import sys, io, os, re, json, math, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
POOL = os.path.join(os.path.dirname(HERE), "brain", "rag_examples.json")
OUT = os.path.join(os.path.dirname(HERE), "brain", "intent_nb.json")
_TOKEN_RX = re.compile(r"[а-яёa-z0-9]{3,}")


def toks(t):
    return [w[:5] for w in _TOKEN_RX.findall((t or "").lower())]


def main():
    data = json.load(open(POOL, encoding="utf-8"))["examples"]
    by = collections.defaultdict(list)
    for ex in data:
        by[ex["class"]].append(ex["q"])
    counts = {c: collections.Counter() for c in by}
    totals = {}
    vocab = set()
    for c, texts in by.items():
        for t in texts:
            for w in toks(t):
                counts[c][w] += 1
                vocab.add(w)
        totals[c] = sum(counts[c].values())
    V = len(vocab)
    n_all = sum(len(v) for v in by.values())
    priors = {c: math.log(len(v) / n_all) for c, v in by.items()}
    # P(w|c) с Лапласом; храним только слова, встречавшиеся >=2 раз суммарно (компактность)
    wtotal = collections.Counter()
    for c in counts:
        wtotal.update(counts[c])
    vocab_out = {}
    for w, tot in wtotal.items():
        if tot < 2:
            continue
        vocab_out[w] = {c: math.log((counts[c][w] + 1) / (totals[c] + V)) for c in by}
    fallback = {c: math.log(1.0 / (totals[c] + V)) for c in by}
    json.dump({"priors": priors, "vocab": vocab_out, "fallback": fallback},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("классов: %d, словарь: %d стемов, примеров: %d -> intent_nb.json"
          % (len(by), len(vocab_out), n_all))
    # быстрая самопроверка
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "brain"))
    import rag
    rag._NB = None
    for q in ("сколько это будет стоить", "приезжайте завтра к обеду", "ул ленина 5 кв 3",
              "а выезд платный?", "дайте ваш номер позвоню", "подумаю ещё, дорого выходит"):
        c, p = rag.classify_intent(q)
        print("  %-38s -> %-12s %.2f" % (q, c, p))


if __name__ == "__main__":
    main()
