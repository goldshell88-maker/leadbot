# -*- coding: utf-8 -*-
"""CRM-клиент против СОХРАНЁННОЙ боевой формы (этап #5, отмашка 16.08).

Образец: ~/Desktop/Work/База/«БТ - Создание заявки.html» — настоящая форма
bt-lead-centre.ru. Офлайн: сеть не трогается, проверяются парсер, подбор
опций по тексту и сборка патча/сериализация.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import crm  # noqa: E402

SAMPLE = os.path.expanduser("~/Desktop/Work/База/БТ - Создание заявки.html")
ok = bad = 0


def chk(label, cond):
    global ok, bad
    if cond:
        ok += 1
    else:
        bad += 1
        print("✗", label)


if not os.path.exists(SAMPLE):
    print("образца нет на этой машине — пропуск всего набора")
    sys.exit(0)

html = open(SAMPLE, encoding="utf-8", errors="replace").read()
form = crm.parse_form(html)

chk("форма найдена", bool(form.fields))
chk("action — создание заявки", form.action.endswith("/customer-request/create"))
for поле in ("Customer[phone]", "CustomerRequest[city_id]",
             "CustomerRequest[appliance_type_id]", "CustomerRequest[is_req_fback]",
             "CustomerRequest[comments]"):
    chk("поле %s" % поле, поле in form.fields)

города = form.selects.get("CustomerRequest[city_id]") or []
chk("города распарсены (>30)", len([o for o in города if o[0]]) > 30)
chk("город по имени: Иркутск", crm.pick_option(города, "Иркутск") != "")
# Артёма в БТ-системе НЕТ — и не должно быть: он только в пуле КП (проверка пулов)
chk("Артема в БТ нет (пулы направлений)", crm.pick_option(города, "Артем") == "")

# «Вид работ» в сырой форме пуст до выбора города — подгружается отдельным
# запросом (_get_appltype=1); здесь проверяем подбор на мок-опциях
виды = [("310", "Холодильники", False), ("311", "Стиральные машины", False),
        ("999", "Прочая", False)]

lead = {
    "city": "Иркутск", "phone": "89261234567", "name": "Иван",
    "address": "Ленина 5, кв 2, подъезд 1, этаж 2",
    "topics": ["холодильник", "стиральн"],
    "partner": "007", "review": True, "call_before_visit": False,
    "comment": "не морозит холодильник\nВыезд и диагностика бесплатны при работах\nСоздана с чата",
}
patch = crm.build_patch(form, lead, appliance_options=виды)
chk("город лёг в патч", patch.get("CustomerRequest[city_id]") != "")
# маска обязательна (пачка 1, 21.08): база принимает только «+7 XXX-XXX-XXXX» —
# сырые цифры валились валидацией (ошибки очереди 16.08, разрыв контракта P0)
chk("телефон лёг в маске", patch.get("Customer[phone]") == "+7 926-123-4567")
chk("вид работ подобран по теме", patch.get("CustomerRequest[appliance_type_id]") != "")
chk("«Отзыв» включён", patch.get("CustomerRequest[is_req_fback]") == "1")
chk("комментарий на месте", "Создана с чата" in patch.get("CustomerRequest[comments]", ""))

body = crm.serialize(form, patch)
chk("сериализация несёт save_close", body.get("save_close") == "1")
chk("дефолты формы не потеряны (type)", "CustomerRequest[type]" in body)
chk("csrf уехал", any(k.startswith("_csrf") for k in body))

# неизвестная тема → «Прочая», а не пустота
lead2 = dict(lead, topics=["муфельная печь ремонт"])
p2 = crm.build_patch(form, lead2, appliance_options=виды)
chk("неизвестная тема → «Прочая»",
    p2.get("CustomerRequest[appliance_type_id]") == "999")

# ── ТЕМЫ ЛИДА: НАСТОЯЩИЕ, А НЕ ПРИДУМАННЫЕ ТЕСТОМ ────────────────────────────
# ⚠ 18.08, P0. Выше в этом файле topics подавались готовыми строками — и тест
# годами зеленел, пока боевой код клал в lead["topics"] РЕЗУЛЬТАТ topics_in:
# кортежи строк прайса с множествами и регексами внутри. В режиме очереди их
# сериализует json.dumps и падает («Object of type set is not JSON
# serializable»), в прямом режиме падает pick_option («'tuple' object has no
# attribute 'lower'»). Заявка не создавалась молча: каждый ЧЕТВЁРТЫЙ лид с
# телефоном (1084 из 4171 по корпусу). Тест подменял ровно ту деталь, где
# ломалось, — поэтому теперь берём темы у настоящего производителя.
import json as _json
import price_book as _pb

_живые_темы = _pb.topics_in("смеситель течёт, ещё розетку поменять и унитаз подтекает")
chk("темы лида — строки, а не строки таблицы прайса",
    bool(_живые_темы) and all(isinstance(t, str) for t in _живые_темы))
try:
    _json.dumps({"topics": _живые_темы})
    chk("темы лида переживают json.dumps (очередь заявок)", True)
except TypeError as e:
    chk("темы лида переживают json.dumps (очередь заявок): %s" % e, False)

_lead_живой = dict(lead, topics=_живые_темы)
try:
    _p3 = crm.build_patch(form, _lead_живой, appliance_options=виды)
    chk("вид работ подбирается по живым темам", _p3.get("CustomerRequest[appliance_type_id]") != "")
except Exception as e:
    chk("вид работ подбирается по живым темам: %s" % e, False)

# кому нужны сами строки прайса — просит явно
chk("полностью=True отдаёт записи прайса",
    all(isinstance(t, tuple) for t in _pb.topics_in("смеситель течёт", полностью=True)))

# ── ВИД РАБОТ: ТЕМА ПРАЙСА ПРОТИВ КАТЕГОРИИ CRM (этап 6б) ───────────────────
# ⚠ pick_option ищет ПОДСТРОКУ, а справочник CRM говорит категориями: «смеситель» в
# «Сантехнике» не содержится, «розетка» в «Электрике» тоже — и заявка уезжала с видом
# работ «Прочая». В боевой очереди уже была родственная запись: «вид работ не подобран:
# опций в подгрузке 0, темы: стиральн».
_КАТЕГОРИИ = [("", "Выберите", False), ("310", "Холодильники", False),
              ("311", "Стиральные машины", False), ("352", "Сантехника", False),
              ("353", "Электрика", False), ("999", "Прочая", False)]
for темы, ждём, имя in (
        (["смеситель", "розетка"], ("352", "353"), "сантехника+электрика"),
        (["стиральн"], ("311",), "стиральная машина"),
        (["холодильник"], ("310",), "холодильник"),
        (["шкаф"], ("999",), "мебели в справочнике нет → честная «Прочая»")):
    _p = crm.build_patch(form, dict(lead, topics=темы), appliance_options=_КАТЕГОРИИ)
    _v = _p.get("CustomerRequest[appliance_type_id]")
    chk("вид работ по категории: %s" % имя, _v in ждём)

print("=" * 50)
print("CRM-форма: %d/%d" % (ok, ok + bad))
sys.exit(1 if bad else 0)
