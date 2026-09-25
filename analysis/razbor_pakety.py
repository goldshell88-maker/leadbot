# -*- coding: utf-8 -*-
"""РАСКЛАДКА КОРПУСА ПО ПАКЕТАМ ДЛЯ МОДЕЛЬНОГО РАЗБОРА. Ноль токенов.

Разбор 27–28.08 шёл этим скриптом: 202 пакета по 200 диалогов. Сами пакеты в git
не лежат (там ПД клиентов) и после разбора удаляются — пересоздать их можно
отсюда за полминуты, результат побайтно тот же при том же корпусе.

Телефоны и ссылки маскируются на выходе: агентам разбора номера не нужны, а в
их ответы попасть не должны.
"""
import json, os, re, hashlib

ТЕЛ = re.compile(r"(?:\+7|8|7)?[\s(-]*9\d{2}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}")
ССЫЛКА = re.compile(r"https?://\S+")
ПАКЕТ = 200
КОРНИ = (("analysis/LEARNING/corpus.jsonl", "messages", "jivo"),
         ("analysis/LEARNING/avito_corpus.jsonl", "реплики", "avito"))


def чистить(t):
    return ССЫЛКА.sub("«фото»", ТЕЛ.sub("«номер»", t or "")).strip()


def город(r):
    г = r.get("город")
    if isinstance(г, str) and г:
        return г[:20]
    p = (r.get("meta") or {}).get("page")
    return p[:20] if isinstance(p, str) else ""


def main(out="analysis/RAZBOR/pak200"):
    корень = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    out = os.path.join(корень, out)
    os.makedirs(out, exist_ok=True)
    пакет, n, всего = [], 0, 0
    for f, поле, канал in КОРНИ:
        for l in open(os.path.join(корень, f), encoding="utf-8"):
            r = json.loads(l)
            реплики = [("К" if m.get("role") == "client" else "М") + ": " + чистить(m.get("text"))
                       for m in (r.get(поле) or [])]
            реплики = [x for x in реплики if len(x) > 3]
            if not реплики:
                continue
            всего += 1
            пакет.append({"id": r.get("id") or r.get("conversation_key")
                          or hashlib.md5(l.encode()).hexdigest()[:10],
                          "к": канал[0], "г": город(r), "д": реплики})
            if len(пакет) >= ПАКЕТ:
                n += 1
                json.dump(пакет, open(os.path.join(out, "q%03d.json" % n), "w", encoding="utf-8"),
                          ensure_ascii=False)
                пакет = []
    if пакет:
        n += 1
        json.dump(пакет, open(os.path.join(out, "q%03d.json" % n), "w", encoding="utf-8"),
                  ensure_ascii=False)
    print("диалогов: %d, пакетов: %d по %d → %s" % (всего, n, ПАКЕТ, out))


if __name__ == "__main__":
    main()
