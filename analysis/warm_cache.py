# -*- coding: utf-8 -*-
"""ПРОГРЕВ КЭША ОТВЕТОВ качественными репликами (04.08.2026).

Зачем. Кэш ответов отдаёт готовую реплику без обращения к модели — это и экономия,
и скорость. Но если в него попала неудачная формулировка, она будет выдаваться снова
и снова. Ключ кэша включает хэш системного промпта, поэтому после правок промпта старые
записи становятся недостижимы — файл при этом продолжает расти мёртвым грузом.

Что делает скрипт:
  1) берёт САМЫЕ ЧАСТЫЕ первые сообщения клиентов из реального корпуса диалогов;
  2) прогоняет каждое через боевой путь бота (run_relay);
  3) проверяет ответ КОНТЕКСТНЫМИ воротами качества (см. gates ниже);
  4) записи, не прошедшие ворота, ВЫРЕЗАЕТ из файла кэша — чтобы плохая формулировка
     не могла всплыть у живого клиента.

Запуск:  python3 analysis/warm_cache.py [сколько_фраз]   (по умолчанию 40)
"""
import collections
import glob
import io
import json
import os
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "brain"))
import prefilter  # noqa
import server     # noqa

def _find_corpus():
    """Корпус диалогов лежит по-разному на Mac и на сервере — ищем в известных местах."""
    for p in (os.environ.get("LEADBOT_CORPUS"),
              os.path.expanduser("~/Work/Jivo Webhook/data/dialogs_merged"),
              "/opt/leadbot-jivo/data/dialogs",        # боевой приёмник на VPS
              "/opt/jivo-webhook/data/dialogs_merged",
              os.path.join(ROOT, "analysis", "dialogs_merged")):
        if p and os.path.isdir(p):
            return p
    return ""


CORPUS = _find_corpus()
CACHE = os.path.join(ROOT, "analysis", "answer_cache.jsonl")

# ── ворота качества: что НЕ должно попадать в кэш ────────────────────────────
CANNED = re.compile(r"сверюсь по график|по график\w* сверю|гляну по времени|чуть позже дам")
EXCUSE = re.compile(r"быстро печата|у меня шаблон|автоответ|работа така")
LECTURE = re.compile(r"давайте спокойно и по делу|ведите себя|не надо так")
LOGIST = re.compile(r"куда (?:мне )?(?:подъехать|приехать|ехать)|по какому адресу|"
                    r"адрес\w* (?:подскаж|назов|скажите)|(?:какой|ваш) адрес|"
                    r"когда (?:вам )?удобно|номер\w* (?:телефона|оставьте)")
ABS_TIME = re.compile(r"\b\d{1,2}[:.]\d{2}\b|\b(?:завтра|сегодня|послезавтра)\b")
ECHO_HINT = re.compile(r"^(?:понял|поняла|понимаю),\s*\w+ (?:не |при )")


def held_topic(client_text):
    """Тема, которую по регламенту УДЕРЖИВАЮТ и передают человеку: согласование, отказные
    позиции («не отказываем никому» → та же отписка), мусорные категории. Для них ответ
    без вопроса и с формулой сверки — ШТАТНОЕ поведение, а не брак."""
    try:
        if prefilter.check_soglasovanie(client_text) or prefilter.check_refuse(client_text) \
                or prefilter.check_junk(client_text, prior_client_content=False):
            return True
        # за чертой города (дача, село, СНТ) — согласование форсится территорией
        import territory
        return bool(territory.settlement_outside(client_text, "Чита"))
    except Exception:
        return False


def gates(client_text, reply):
    """Возвращает список нарушений (пусто = ответ годен к кэшированию)."""
    bad, low = [], (reply or "").lower()
    held = held_topic(client_text)
    if not (reply or "").strip():
        bad.append("пустой ответ")
        return bad
    # формула сверки плоха, когда она ВМЕСТО разговора: на офтопе — всегда, на рабочей теме —
    # если реплика целиком отписка (ни принятия, ни вопроса). «Принтер беру, а по СНПЧ сверюсь»
    # — это законная сверка по ОДНОЙ позиции, брать её в брак нельзя.
    _offtop = bool(prefilter._OFFTOPIC_RX.search(client_text or ""))
    if CANNED.search(low) and (_offtop or (not held and "?" not in reply)):
        bad.append("заготовка согласования не по теме")
    if EXCUSE.search(low):
        bad.append("оправдание («быстро печатаю»)")
    if LECTURE.search(low):
        bad.append("нотация")
    if LOGIST.search(low) and not prefilter.problem_stated(client_text):
        bad.append("зовёт на выезд до того, как названа поломка")
    if ABS_TIME.search(low):
        bad.append("абсолютное время — протухнет в кэше")
    if ECHO_HINT.search(low):
        bad.append("эхо проблемы клиента")
    if "?" not in reply and not held \
            and not re.search(r"запис|до встречи|всего доброго", low):
        bad.append("нет вопроса и нет финала — воронка встала")
    return bad


def top_openers(n):
    """Самые частые ПЕРВЫЕ реплики клиентов в реальном корпусе."""
    cnt = collections.Counter()
    for f in sorted(glob.glob(os.path.join(CORPUS, "*.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for m in (d.get("messages") or []):
            if m.get("role") == "client" and (m.get("text") or "").strip():
                t = re.sub(r"\s+", " ", m["text"]).strip()
                if 4 <= len(t) <= 200:
                    cnt[t] += 1
                break
    return cnt.most_common(n)


def ask(text, kn):
    for att in range(5):
        try:
            return server.run_relay([{"role": "client", "text": text}], known=kn)
        except Exception as ex:
            if any(s in str(ex) for s in ("529", "429")) or "overload" in str(ex).lower():
                time.sleep(15 * (att + 1))
                continue
            raise
    raise RuntimeError("API перегружен")


def purge(bad_keys):
    """Вырезает из файла кэша записи с указанными ключами."""
    if not bad_keys or not os.path.exists(CACHE):
        return 0
    keep, dropped = [], 0
    for line in open(CACHE, encoding="utf-8"):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("k") in bad_keys:
            dropped += 1
            continue
        keep.append(line.rstrip("\n"))
    with open(CACHE, "w", encoding="utf-8") as f:
        f.write("\n".join(keep) + ("\n" if keep else ""))
    return dropped


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    kn = {"visitor": "Клиент", "city": "Чита", "direction": "bt"}
    if not CORPUS:
        print("Корпус диалогов не найден — укажите путь в LEADBOT_CORPUS")
        return
    openers = top_openers(n)
    print("Корпус: %s" % CORPUS)
    print("Прогрев кэша: %d самых частых первых сообщений клиентов\n" % len(openers))

    good, bad_keys, failed = 0, set(), []
    for i, (text, freq) in enumerate(openers, 1):
        r = ask(text, kn)
        reply = (r.get("reply") or "").strip()
        problems = gates(text, reply)
        # ключ считаем ровно как боевой путь: только «значимые» поля карточки
        kr = {k: kn.get(k) for k in ("visitor", "city", "title", "direction",
                                     "persona", "partner", "white")}
        key = server._answer_key([{"role": "client", "text": text}], kr)
        if problems:
            bad_keys.add(key)
            failed.append((text, reply, problems))
            mark = "✗"
        else:
            good += 1
            mark = "✓"
        print("%s [%3d×] %s" % (mark, freq, text[:70]))
        print("      %s" % (reply[:120].replace("\n", " / ") or "(пусто)"))
        if problems:
            print("      ⚠ %s" % "; ".join(problems))

    dropped = purge(bad_keys)
    print("\n" + "=" * 58)
    print("Годных ответов: %d из %d" % (good, len(openers)))
    if failed:
        print("Выбраковано и удалено из кэша: %d записей" % dropped)
        for t, rep, pr in failed:
            print("  • %s → %s" % (t[:50], "; ".join(pr)))
    total = sum(1 for l in open(CACHE, encoding="utf-8") if l.strip()) if os.path.exists(CACHE) else 0
    print("В кэше сейчас записей: %d" % total)


if __name__ == "__main__":
    main()
