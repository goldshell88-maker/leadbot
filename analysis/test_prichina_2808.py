# -*- coding: utf-8 -*-
"""ПРИЧИНА ПЕРЕДАЧИ ОБЯЗАНА БЫТЬ ПРАВДОЙ (замер по боевой базе панели 28.08).

В базе LeadChat 414 записей бота. Из них 117 — передачи диалога человеку, и у 84
причина «AI не уверен в ответе». На деле уверенность у бота бывает только трёх
значений: 1.0 (роутер), 0.9 (модель ответила), 0.0 (ответа нет). Порог панели 0.6,
то есть «низкая уверенность» = «ответа не было вовсе», а вовсе не сомнение модели.

Откуда брались 84. Событие эскалации собирается в общем хвосте `_finish`, а роутеры
возвращают словарь НАПРЯМУЮ — мимо него. Панель читает причину из `meta.escalation`
и, не найдя её, подписывает передачу заглушкой. По журналу боевого сервера так
подписано 311 случаев «не наш диалог» за две недели: правильное поведение выглядит
как ошибка ИИ, а оператор сортирует очередь именно по причине.
"""
import io, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "brain"))
import escalation, leadchat

ok = fail = 0


def проверить(имя, условие, факт=""):
    global ok, fail
    print(("  OK  " if условие else "  FAIL") + "  " + имя + ("" if условие else "  → " + str(факт)))
    if условие:
        ok += 1
    else:
        fail += 1


print("── ПРИЧИНА ДОХОДИТ ДО ПАНЕЛИ ────────────────────────────────────")
for имя, res, ждём in (
        ("не наш диалог", {"reply": "", "handoff": True, "notify_reason": "not_primary",
                           "escalation": escalation.event("not_primary", "диалог ведёт человек"),
                           "model": "локальный роутер (не наш диалог)"}, "not_primary"),
        ("голосовое", {"reply": "Пришлите текстом", "handoff": True, "notify_reason": "voice",
                       "escalation": escalation.event("voice", "голосовое без текста"),
                       "model": "локальный роутер (голосовое)"}, "voice"),
        ("нестандартный вопрос", {"reply": "", "to_operator": True, "handoff": True,
                                  "notify_reason": "unknown_topic",
                                  "escalation": escalation.event("unknown_topic", "нестандартный"),
                                  "model": "модель"}, "unknown_topic"),
        ("клиент звонил", {"reply": "", "handoff": True, "notify_reason": "call_made",
                           "escalation": escalation.event("call_made", "клиент звонил"),
                           "model": "локальный роутер (клиент звонит)"}, "call_made"),
        ("модель промолчала", {"reply": "", "to_operator": True, "handoff": True,
                               "notify_reason": "model_silent",
                               "escalation": escalation.event("model_silent", "модель молчит"),
                               "model": "модель"}, "model_silent")):
    a = leadchat.to_answer(res, ms=1, request_id="t")
    факт = ((a.get("meta") or {}).get("escalation") or {}).get("reason")
    проверить(имя, факт == ждём, факт)

print("── РАННИЕ ВОЗВРАТЫ НЕСУТ ПРИЧИНУ (проводка в коде) ──────────────")
# ⚠ Проверяем ИСХОДНИК: тест выше работает на собранных вручную словарях и сам по
# себе не докажет, что боевой путь их формирует. Здесь смотрим сами возвраты.
# ⚠ РАЗБОР ДЕРЕВА, А НЕ РЕГЕКС. Первая редакция искала «return {…notify_reason…}»
# регулярным выражением, и `[^}]*` обрывался на ВЛОЖЕННОМ словаре `"flag": {…}`.
# Проверка осталась зелёной при вырезанной эскалации — то есть не проверяла ничего.
import ast as _ast
_src = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                            "brain", "server.py"), encoding="utf-8").read()
_дерево = _ast.parse(_src)
_без, _всего = [], 0
for _узел in _ast.walk(_дерево):
    if not isinstance(_узел, _ast.Return) or not isinstance(_узел.value, _ast.Dict):
        continue
    ключи = {k.value for k in _узел.value.keys
             if isinstance(k, _ast.Constant) and isinstance(k.value, str)}
    if "notify_reason" not in ключи:
        continue
    # причина в этом возврате названа константой? None и переменные пропускаем
    причина = None
    for k, v in zip(_узел.value.keys, _узел.value.values):
        if isinstance(k, _ast.Constant) and k.value == "notify_reason":
            причина = v.value if isinstance(v, _ast.Constant) else "<выражение>"
    if причина in (None, "<выражение>"):
        continue
    _всего += 1
    if "escalation" not in ключи:
        _без.append("%s (строка %d)" % (причина, _узел.lineno))
проверить("каждый ранний возврат с причиной несёт escalation", not _без, _без)
проверить("возвраты с причиной вообще найдены", _всего >= 5, _всего)

print()
print("итог: ok=%d fail=%d" % (ok, fail))
sys.exit(1 if fail else 0)
