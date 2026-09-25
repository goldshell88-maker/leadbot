# -*- coding: utf-8 -*-
"""ПРИЧИНА ЭСКАЛАЦИИ НЕ ВРЁТ (находка 23.08).

Замер боевого журнала воронки перед починкой: 194 передачи человеку из 194 шли с
ПУСТЫМ флагом. Пустой флаг в `_contract` означал ветку «иначе» — `unknown_topic`,
ярлык «тема не распознана». То есть каждый диалог, который бот отдал человеку за
двадцать дней, приходил в очередь с одной и той же неправдой: код, отдавший диалог,
причину знал точно — закрытый филиал, село за чертой, нет филиала направления.

Цена не косметическая: причина едет в `meta.escalation` LeadChat, в журнал работы
(`escalation_reason`) и в сторож просрочки `channel._overdue_watch`, который берёт
по ней срок реакции. Один слаг на всё = один срок на всё и ярлык, который оператор
перестаёт читать раньше, чем он понадобится.

Офлайн, 0 токенов: шлюз подменён заглушкой (см. `_stub`). Запуск:
    python3 analysis/test_escalation_reason.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "brain"))
import claude_api as _ca                            # noqa: E402
import escalation as esc                            # noqa: E402
import server as s                                  # noqa: E402

СЛУЧАИ = []


def chk(name, got, want):
    СЛУЧАИ.append((name, got, want))


def _stub(**tool_input):
    """Ответ шлюза в СЫРОЙ форме Anthropic — именно её возвращает claude_api.messages."""
    base = {"reply": "приму, подскажите адрес", "city": "", "phone": "", "address": "",
            "service": "", "preferred_time": "", "category": "bt",
            "ready_to_create": False, "handoff_to_human": False, "pass_to_operator": False}
    base.update(tool_input)

    def _messages(*_a, **_kw):
        return {"content": [{"type": "tool_use", "name": "dispatcher_turn", "input": dict(base)}],
                "usage": {}, "model": "offline-stub"}

    _ca.messages = _messages


def причина(текст, known=None, **stub):
    """Причина эскалации на боевом ходу целиком, а не на изолированной функции."""
    _stub(**stub)
    # ⚠ КЭШ ОТВЕТОВ ОБЩИЙ НА ПРОЦЕСС. Без сброса второй случай с тем же текстом
    # приходит по кэш-ветке, минуя заглушку, и тест проверял бы не то, что думает.
    try:
        s._ANSWER_CACHE.clear()
    except Exception:
        pass
    r = s.run_relay([{"role": "client", "text": текст}], known=known or {})
    return (r.get("escalation") or {}).get("reason"), r


# ─────────── 1. МОДЕЛЬНЫЙ ПУТЬ: семь форсов, каждый со своей причиной ───────
ЗАКРЫТЫЙ = {"city": "Петрозаводск", "direction": "bt"}
r, _ = причина("Не морозит холодильник, Ленина 5", known=ЗАКРЫТЫЙ)
chk("закрытый филиал → согласование", r, "soglasovanie")

НЕТ_ФИЛИАЛА = {"city": "Красногорск", "direction": "bt"}
r, res = причина("Стиральная машина не отжимает, приезжайте", known=НЕТ_ФИЛИАЛА)
chk("нет филиала направления → согласование", r, "soglasovanie")

r, _ = причина("Не морозит, я в деревне Кузнецово, дом 8", known={"city": "Москва"})
chk("село за чертой города → согласование", r, "soglasovanie")

# пустой ответ шлюза — сбой, а не «тема не распознана»: клиенту не ушло НИЧЕГО
r, res = причина("Не включается телевизор", reply="")
chk("шлюз промолчал → model_silent", r, "model_silent")
chk("шлюз промолчал: клиенту пусто", res["reply_text"], "")

# модель сама сказала «не понимаю» — вот здесь unknown_topic честен
r, _ = причина("Вопрос не по теме совсем", pass_to_operator=True)
chk("модель не поняла → unknown_topic честен", r, "unknown_topic")

r, _ = причина("Не крутит барабан, что делать", handoff_to_human=True)
chk("модель позвала человека → model_handoff", r, "model_handoff")


# ─────────── 2. РОУТЕРЫ: карта видов флага в _contract ──────────────────────
def по_флагу(kind, sub=None):
    res = {"reply": "" if kind == "blocklist" else "текст", "handoff": True,
           "to_operator": kind == "blocklist",
           "flag": {"kind": kind, "sub": sub, "reason": "проверка"}}
    return (s._contract(res).get("escalation") or {}).get("reason")


КАРТА = [
    ("blocklist", None, "blocklist"),
    ("multi_account", None, "multi_account"),
    ("troll_exit", None, "troll_exit"),
    ("complaint", None, "claim"),
    ("existing_client", None, "status"),
    ("call_request", None, "call_request"),
    ("soglas", "матрица", "soglasovanie"),
    ("escalation", "no_show", "no_show"),
]
for kind, sub, want in КАРТА:
    chk("роутер %s → %s" % (kind, want), по_флагу(kind, sub), want)

# отказ и мусор человека не зовут вовсе — причины быть не должно
for kind in ("refuse", "junk"):
    res = s._contract({"reply": "текст", "handoff": True, "flag": {"kind": kind}})
    chk("%s: человека не зовём" % kind, res["notify_human"], False)
    chk("%s: причины нет" % kind, res["escalation"], None)


# ─────────── 3. СЛОВАРЬ ПРИЧИН ПОЛОН ────────────────────────────────────────
# Ярлык строится как REASONS.get(reason, reason): пропущенный слаг не падает, а
# молча уезжает к оператору сырым — «model_silent» вместо человеческой фразы.
ВСЕ = {w for _, _, w in КАРТА} | {"soglasovanie", "model_silent", "unknown_topic",
                                  "model_handoff", "offtopic"}
for сл in sorted(ВСЕ):
    chk("ярлык есть: " + сл, сл in esc.REASONS, True)
    chk("срок задан явно: " + сл, сл in esc.DEADLINES, True)

# молчаливые причины — самые срочные: клиент не получил ни слова
chk("молчание не ждёт полчаса", esc.deadline_min("unknown_topic") <= 10, True)
chk("сбой шлюза не ждёт полчаса", esc.deadline_min("model_silent") <= 10, True)
# информационные не будят сторожа просрочки
for сл in ("blocklist", "multi_account", "troll_exit"):
    chk("информационная терпит: " + сл, esc.deadline_min(сл) >= 120, True)


# ─────────── 4. СТОРОЖ ИСХОДНИКА: передача без причины не компилируется глазами ──
# Причина забывалась ровно потому, что передача была ОДНИМ присваиванием. Пока их
# ноль, забыть нельзя: _передать требует причину вторым доводом.
_ИСХ = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                         "brain", "server.py"), encoding="utf-8").read()
_ТЕЛО = _ИСХ[_ИСХ.index("def _run_relay_inner"):]
_ГОЛЫЕ = re.findall(r'^\s*res\["handoff"\]\s*=\s*True', _ТЕЛО, re.M)
chk("в _run_relay_inner нет передачи без причины", len(_ГОЛЫЕ), 0)
chk("_передать требует причину", s._передать.__code__.co_argcount, 2)


# ─────────── итог ───────────────────────────────────────────────────────────
плохо = [(n, g, w) for n, g, w in СЛУЧАИ if g != w]
print("=" * 60)
for n, g, w in плохо:
    print("ПРОВАЛ  %-46s получили %r, ждали %r" % (n, g, w))
print("Причина эскалации: %d/%d" % (len(СЛУЧАИ) - len(плохо), len(СЛУЧАИ)))
sys.exit(1 if плохо else 0)
