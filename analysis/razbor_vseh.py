# -*- coding: utf-8 -*-
"""ПОЛНЫЙ ДЕТЕРМИНИРОВАННЫЙ РАЗБОР КАЖДОГО ДИАЛОГА КОРПУСА. Ноль токенов.

По каждому из 40 268 диалогов пишется запись: кто начал, сколько ходов, что клиент
дал, что мастер сделал, кто оборвал разговор и на чём. Модель здесь не нужна — это
факты переписки, а не толкование. Толкование («почему клиент так ответил») идёт
отдельным модельным разбором тех же пакетов.

Выход: analysis/RAZBOR/vse.jsonl (одна строка на диалог) + сводка в stdout.
"""
import sys, os, json, re, collections
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "brain"))
import prefilter, server as s

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "RAZBOR", "vse.jsonl")

ЦИФРА = re.compile(r"\d{3,5}\s*(?:руб|р\b|₽)|от\s+\d{3,5}", re.I)
ОКНО = re.compile(r"могу\s+(?:сегодня|завтра|подъехать|приехать)|в\s+течение\s+часа|через\s+час|"
                  r"сегодня\s+к\s+\d|завтра\s+к\s+\d|подъеду\s+(?:сегодня|завтра)", re.I)
ОТКАЗ_КЛ = re.compile(r"^\s*нет[,\s.!]|не\s+надо|не\s+нужно|уже\s+(?:не|решил|нашёл|нашел|сделал)|"
                      r"спасибо,?\s+не\b|передумал|дорого,?\s+не", re.I)
ЖАЛОБА = re.compile(r"отзыв|верните\s+деньги|вернуть\s+деньги|жалоб|роспотреб|\bсуд\b|претенз|"
                    r"ваш\s+мастер|после\s+(?:ремонта|визита)|хуже\s+стало|опять\s+не\s+работает", re.I)
ПЕРЕДАЧА = re.compile(r"перевод\s+на\s+оператора|оператор\s+подключ|передаю\s+(?:вас\s+)?(?:коллеге|мастеру)", re.I)
СЛУЖЕБНОЕ = re.compile(r"^\s*(?:вам\s+помощь\s+актуальна|чат\s+(?:закрыт|завершен))", re.I)


def ходы(реплики):
    out = []
    for m in реплики:
        r, t = m.get("role"), (m.get("text") or "").strip()
        if not t:
            continue
        if out and out[-1][0] == r:
            out[-1][1].append(t)
        else:
            out.append([r, [t]])
    return out


def разобрать(r, поле, канал):
    h = ходы(r.get(поле) or [])
    if not h:
        return None
    кл = [" ".join(b) for role, b in h if role == "client"]
    оп = [" ".join(b) for role, b in h if role == "operator"]
    хвост = " \n".join(кл[1:]) if len(кл) > 1 else ""
    все_оп = " \n".join(оп)
    дал_номер = bool(prefilter.phones_in(хвост))
    дал_адрес = bool(s._ADDR_RX.search(хвост))
    # кто оборвал: чей ход последний. Служебные реплики оператора не считаем.
    последний = None
    for role, b in reversed(h):
        if role == "operator" and СЛУЖЕБНОЕ.search(" ".join(b)):
            continue
        последний = role
        break
    # исход. Порядок важен: жалоба перекрывает всё, заявка сильнее молчания.
    if ЖАЛОБА.search(" \n".join(кл)):
        исход = "жалоба"
    elif дал_номер and дал_адрес:
        исход = "заявка"
    elif ПЕРЕДАЧА.search(все_оп):
        исход = "передача"
    elif кл and ОТКАЗ_КЛ.search(кл[-1]):
        исход = "отказ клиента"
    elif not оп:
        исход = "мастер не ответил"
    elif последний == "operator":
        исход = "клиент замолчал"
    else:
        исход = "мастер замолчал"
    return {
        "id": r.get("id") or r.get("conversation_key") or "",
        "канал": канал,
        "город": r.get("город") if isinstance(r.get("город"), str) else "",
        "ходов_клиента": len(кл),
        "ходов_мастера": len(оп),
        "первый_клиента_знаков": len(кл[0]) if кл else 0,
        "дал_номер": дал_номер,
        "дал_адрес": дал_адрес,
        "мастер_назвал_цифру": bool(ЦИФРА.search(все_оп)),
        "мастер_дал_окно": bool(ОКНО.search(все_оп)),
        "мастер_молчал": not оп,
        "оборвал": последний or "",
        "исход": исход,
    }


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    сч = collections.Counter()
    длина = collections.Counter()
    заявки_по_ходам = collections.defaultdict(lambda: [0, 0])
    n = 0
    with open(OUT, "w", encoding="utf-8") as w:
        for f, поле, канал in (("analysis/LEARNING/corpus.jsonl", "messages", "jivo"),
                               ("analysis/LEARNING/avito_corpus.jsonl", "реплики", "avito")):
            путь = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", f)
            for l in open(путь, encoding="utf-8"):
                зап = разобрать(json.loads(l), поле, канал)
                if not зап:
                    continue
                n += 1
                w.write(json.dumps(зап, ensure_ascii=False) + "\n")
                сч[зап["исход"]] += 1
                длина[min(зап["ходов_клиента"], 8)] += 1
                к = заявки_по_ходам[min(зап["ходов_мастера"], 8)]
                к[1] += 1
                к[0] += зап["исход"] == "заявка"
    print("РАЗОБРАНО ДИАЛОГОВ: %d   →   %s" % (n, OUT))
    print()
    print("ЧЕМ КОНЧАЮТСЯ ДИАЛОГИ")
    for k, v in сч.most_common():
        print("  %-18s %6d  (%4.1f %%)" % (k, v, 100.0 * v / n))
    print()
    print("СКОЛЬКО РАЗ ПИШЕТ КЛИЕНТ")
    for k in sorted(длина):
        print("  %-8s %6d  (%4.1f %%)" % ("%d раз" % k if k < 8 else "8+", длина[k], 100.0 * длина[k] / n))
    print()
    print("ДОЛЯ ЗАЯВОК ОТ ЧИСЛА ХОДОВ МАСТЕРА")
    for k in sorted(заявки_по_ходам):
        а, б = заявки_по_ходам[k]
        if б < 50:
            continue
        print("  %-8s %5.1f %%   (%d диалогов)" % ("%d ходов" % k if k < 8 else "8+", 100.0 * а / б, б))


if __name__ == "__main__":
    main()
