# -*- coding: utf-8 -*-
"""КЛИЕНТ CRM ЛИД-ЦЕНТРОВ (этап #5, отмашка заказчика 16.08).

Три системы на одном движке Yii2 — bt/kp/mnc-lead-centre.ru. Механика записи
скопирована с «Заявок Хаба» (проверена месяцами ручной работы):

    GET формы создания → патч ТОЛЬКО наших полей → сериализация ВСЕЙ формы
    (серверные дефолты и скрытые поля не теряются) → POST с save_close.

Новый id заявки приходит редиректом на карточку (…update?id=N). Ошибки
валидации — блоками .invalid-feedback / .alert-danger в ответном HTML.

⚠ РЕЖИМЫ (config.json → crm.autocreate): "off" — модуль спит; "dry" —
собираем и валидируем патч, логируем, НО НЕ ПОСТИМ (боевой прогон без
последствий); "on" — создаём по-настоящему. По умолчанию "off".

⚠ Сессии: свой вход по логину/паролю диспетчерской учётки (config.json →
crm.systems.<key>.login/password). Cookie живёт в памяти процесса,
протухла — входим заново. Никакие креды в журнал не пишутся.
"""

import http.cookiejar
import json
import re
import time
import urllib.parse
import urllib.error
import urllib.request
from html.parser import HTMLParser

import paths
import price_book

# ── справочники базы (выгружены со страницы создания и карточки, 16.08) ──
# Тип заявки решает ИСТОРИЯ КЛИЕНТА В БАЗЕ (правило владельца 16.08):
# есть закрытая заявка «Готово»/«Отказ» → «Повтор»; отмены не считаются.
# Слова о гарантии в диалоге → «Гарантия». Иначе (и новый клиент) — «Впервые».
# Слова клиента о нашем визите (repeat_hint) — лишь пометка в попапе.
REQUEST_TYPES = {"10": "Впервые", "20": "Повтор", "30": "Гарантия"}
# Непрофильность: бот всегда «Обычная» (непрофиль мы не принимаем — П.12).
NONCORE = {"0": "Обычная", "1": "Непрофильная", "50": "Обычная 25"}
# Статусы жизненного цикла (карточка): создаётся в «Ожидает»; дальше людьми —
# «Взять в работу», закрытия «Отказались»/«Отмена КЦ»/«Отмена Филиал»/
# «Не оформлена». Бот статусы НЕ трогает никогда.

BASES = {
    "bt": "https://bt-lead-centre.ru",
    "kp": "https://kp-lead-centre.ru",
    "mnc": "https://mnc-lead-centre.ru",
}
CREATE_PATH = "/admin/domain/customer-request/create"
LOGIN_PATH = "/admin/default/index"
SEARCH_PATH = "/api/customer/search"
UA = "Mozilla/5.0 (X11; Linux x86_64) Chrome/126 leadbot-crm"


def config():
    try:
        with open(paths.CONFIG_FILE, encoding="utf-8") as f:
            return (json.load(f).get("crm") or {})
    except Exception:
        return {}


def mode():
    return (config().get("autocreate") or "off").strip()


# ── разбор HTML-формы (stdlib, без bs4 на сервере) ────────────────────────────────
class _FormParser(HTMLParser):
    """Вытаскивает поля формы по id: inputs/selects/textarea со значениями.

    Селекты хранят и список опций — по нему подбираются city_id, партнёр и
    вид работ (по тексту, как их видит человек)."""

    def __init__(self, form_id):
        super().__init__(convert_charrefs=True)
        self.form_id = form_id
        self.in_form = False
        self.depth = 0
        self.fields = {}          # name -> {"value": str, "type": str}
        self.selects = {}         # name -> [(value, label, selected)]
        self._sel = None
        self._opt = None
        self._ta = None
        self.action = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            if a.get("id") == self.form_id:
                self.in_form, self.depth = True, 1
                self.action = a.get("action") or ""
            elif self.in_form:
                self.depth += 1
            return
        if not self.in_form:
            return
        if tag == "input":
            name = a.get("name")
            if not name:
                return
            typ = (a.get("type") or "text").lower()
            if typ in ("checkbox", "radio"):
                cur = self.fields.get(name)
                if "checked" in a:
                    self.fields[name] = {"value": a.get("value", "1"), "type": typ}
                elif cur is None:
                    # Yii шлёт скрытый input перед checkbox — его значение и есть «выключено»
                    self.fields.setdefault(name, {"value": a.get("value", ""), "type": typ})
            else:
                self.fields[name] = {"value": a.get("value", ""), "type": typ}
        elif tag == "select":
            self._sel = a.get("name") or ""
            self.selects.setdefault(self._sel, [])
            self.fields.setdefault(self._sel, {"value": "", "type": "select"})
        elif tag == "option" and self._sel is not None:
            self._opt = {"value": a.get("value", ""), "selected": "selected" in a, "label": ""}
        elif tag == "textarea":
            self._ta = {"name": a.get("name") or "", "buf": []}
            self.fields.setdefault(self._ta["name"], {"value": "", "type": "textarea"})

    def handle_endtag(self, tag):
        if tag == "form" and self.in_form:
            self.depth -= 1
            if self.depth <= 0:
                self.in_form = False
        elif tag == "option" and self._opt is not None and self._sel is not None:
            o = self._opt
            self.selects[self._sel].append((o["value"], o["label"].strip(), o["selected"]))
            if o["selected"]:
                self.fields[self._sel] = {"value": o["value"], "type": "select"}
            self._opt = None
        elif tag == "select":
            self._sel = None
        elif tag == "textarea" and self._ta is not None:
            self.fields[self._ta["name"]] = {"value": "".join(self._ta["buf"]), "type": "textarea"}
            self._ta = None

    def handle_data(self, data):
        if self._opt is not None:
            self._opt["label"] += data
        elif self._ta is not None:
            self._ta["buf"].append(data)


def parse_form(html, form_id="customerRequestForm"):
    p = _FormParser(form_id)
    p.feed(html)
    return p


def form_errors(html):
    """Тексты .invalid-feedback и .alert-danger из ответа (регэкспами — без DOM)."""
    out = []
    for m in re.finditer(r'class="[^"]*invalid-feedback[^"]*"[^>]*>(.*?)</', html, re.S):
        t = re.sub(r"<[^>]+>", " ", m.group(1)).strip()
        if t:
            out.append(t)
    for m in re.finditer(r'class="[^"]*alert-(?:danger|error)[^"]*"[^>]*>(.*?)</div>', html, re.S):
        t = re.sub(r"<[^>]+>", " ", m.group(1)).strip()
        if t:
            out.append(t[:200])
    return out


#: ⚠ КЛИЕНТ И СПРАВОЧНИК CRM ГОВОРЯТ РАЗНЫМИ СЛОВАМИ (28.08, заявка ТЕСТ5 №1234568).
#: Клиент пишет «телевизор», а в справочнике КП Москвы стоит «ЖК ТВ/Плазменные ТВ/
#: Кинескопные ТВ» — слова «телевизор» там нет. Подбор не находил ничего и уходил в
#: «** Прочая», то есть в позицию «этим мы не занимаемся, только по согласованию».
#: Список составлен по НАСТОЯЩЕМУ справочнику от расширения (10 позиций КП Москвы),
#: а не по догадкам. Пробуется ПОСЛЕ самой темы и ДО «прочей»: своё имя сильнее
#: подсказки. Лишний синоним безвреден — такой подписи просто не найдётся.
#: Тот же список стоит в расширении (ui/crm-extension/offscreen.js): путей создания два.
СИНОНИМЫ = (
    (r"^телевизор|^телек|^тв$|плазм|кинескоп", ("жк тв", "тв")),
    (r"^ноутбук|^компьютер|^пк$|моноблок|систем.?ник|материнск",
     ("компьютер", "ноутбук", "моноблок")),
    (r"^принтер|^мфу|печат|картридж", ("принтер", "мфу")),
    (r"^смартфон|^телефон|^айфон|^планшет", ("смартфон", "планшет")),
    (r"^роутер|вай.?фай|wi.?fi|^интернет", ("роутер", "обжим")),
    (r"^монитор|видеокарт", ("монитор", "видеокарт")),
    (r"пристав|playstation|^ps[0-9]?$|xbox|nintendo|steam",
     ("playstation", "xbox", "nintendo", "steam")),
    (r"жестк.?диск|жёстк.?диск|флеш|восстанов.*(информ|данн)|^ssd$|^hdd$",
     ("восстановление информации", "восстановление")),
)


def синонимы_темы(тема):
    """Как эту же вещь называет справочник CRM. Пусто — синонимов нет."""
    t = (тема or "").lower().replace("ё", "е").strip()
    if not t:
        return ()
    for шаблон, варианты in СИНОНИМЫ:
        if re.search(шаблон, t, re.IGNORECASE):
            return варианты
    return ()


def pick_option(options, *wanted):
    """value опции, чей текст НАЧИНАЕТСЯ СО СЛОВА с этой основой; '' если нет.

    ⚠ ГРАНИЦА СЛОВА, А НЕ ПРОСТО ПОДСТРОКА (26.08). Тема «кран» находилась внутри
    подписи «Замена ЭКРАНА», и заявка на телевизор уезжала с видом работ по сантехнике.
    Тот же класс, что «тв» внутри «здравствуйте» на стороне бота и «7» внутри «_197»
    у партнёра, — и лечится так же: слева от совпадения не должно быть буквы.
    Хвост не трогаем: темы это ОСНОВЫ и обязаны брать окончания подписи
    («стиральн» → «Стиральные машины»).
    """
    for w in wanted:
        wl = (w or "").lower().replace("ё", "е").strip()
        if not wl:
            continue
        for value, label, _sel in options:
            if not value:
                continue
            текст = (label or "").lower().replace("ё", "е")
            i = текст.find(wl)
            while i >= 0:
                if i == 0 or not текст[i - 1].isalpha():
                    return value
                i = текст.find(wl, i + 1)
    return ""


def pick_number(options, номер):
    """Опция, ЧИСЛО которой равно заданному. Для справочников, где значение — номер.

    ⚠ ПОДСТРОКОЙ НОМЕР ИСКАТЬ НЕЛЬЗЯ (жалоба владельца на заявку №1234567: партнёр
    007 уехал в карточку как «_197»). Поиск шёл через pick_option подстрокой «7» —
    и «7» честно нашлась внутри «_197», первой же по списку. Обрамление пробелами
    не спасало: pick_option делает .strip() и превращает « 7 » обратно в «7».
    Сравниваем ЧИСЛА: из ярлыка берём все цифры, ведущие нули убираем с обеих
    сторон, «007» = «7» = «7», а «_197» = «197» ≠ «7».
    """
    n = re.sub(r"\D", "", str(номер or "")).lstrip("0")
    if not n:
        return ""
    for value, label, _sel in options:
        if not value:
            continue
        ц = re.sub(r"\D", "", label or "").lstrip("0")
        if ц and ц == n:
            return value
    return ""


# ── HTTP-сессии по системам ───────────────────────────────────────────────────────
_OPENERS = {}   # srcKey -> (opener, logged_at)


def _opener(src):
    op = _OPENERS.get(src)
    if op:
        return op[0]
    jar = http.cookiejar.CookieJar()
    o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    o.addheaders = [("User-Agent", UA)]
    _OPENERS[src] = (o, 0)
    return o


def _get(src, path, timeout=25):
    url = BASES[src] + path
    with _opener(src).open(url, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace"), r.geturl()


def _post(src, path, data, timeout=30):
    body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
    req = urllib.request.Request(
        BASES[src] + path, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                 "X-Requested-With": "XMLHttpRequest", "User-Agent": UA})
    try:
        with _opener(src).open(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace"), r.geturl()
    except urllib.error.HTTPError as e:
        # Yii-диалект: на AJAX-POST успех приходит как 302 + заголовок
        # X-Redirect БЕЗ Location — urllib падал, и режим "on" считал
        # созданную заявку сбоем (аудит 16.08; расширение это уже умеет)
        if e.code == 302:
            redir = e.headers.get("X-Redirect") or e.headers.get("Location") or ""
            return "", (BASES[src] + redir if redir.startswith("/") else redir)
        raise


def _looks_like_login(html):
    return bool(re.search(r'name="LoginForm\[|type="password"', html))


def login(src):
    """Вход диспетчерской учёткой. Поля логин-формы берём из самой страницы."""
    cfg = (config().get("systems") or {}).get(src) or {}
    user, pwd = (cfg.get("login") or "").strip(), (cfg.get("password") or "").strip()
    if not user or not pwd:
        raise RuntimeError("нет логина/пароля CRM %s в config.json → crm.systems" % src)
    html, _u = _get(src, LOGIN_PATH)
    if not _looks_like_login(html):
        return True                        # сессия уже жива
    p = parse_form_any(html)
    data = {}
    for name, f in p.fields.items():
        data[name] = f["value"]
    ukey = next((n for n in p.fields if re.search(r"username|login|email", n, re.I)), None)
    pkey = next((n for n in p.fields if re.search(r"password", n, re.I)), None)
    if not ukey or not pkey:
        raise RuntimeError("логин-форма CRM %s не распознана" % src)
    data[ukey], data[pkey] = user, pwd
    html2, u2 = _post(src, p.action or LOGIN_PATH, data)
    if _looks_like_login(html2):
        raise RuntimeError("CRM %s не принял логин (проверьте учётку)" % src)
    return True


def parse_form_any(html):
    """Первая форма на странице (для логина, где id формы не наш)."""
    m = re.search(r'<form[^>]*id="([^"]+)"', html)
    return parse_form(html, m.group(1) if m else "")


def _ensure_session(src):
    html, _u = _get(src, CREATE_PATH)
    if _looks_like_login(html):
        login(src)
        html, _u = _get(src, CREATE_PATH)
        if _looks_like_login(html):
            raise RuntimeError("CRM %s: после входа форма создания недоступна" % src)
    return html


def load_appliance_options(src, city_id):
    """«Вид работ» зависит от города: живой фрагмент <option> подгружается тем же
    механизмом, что у нативной формы (_get_appltype=1&city_id=N)."""
    q = urllib.parse.urlencode({"_get_appltype": 1, "city_id": city_id,
                                "appl_id": "", "subappl_id": "", "has_partner": 0})
    html, _u = _get(src, CREATE_PATH + "?" + q)
    out = []
    for m in re.finditer(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', html, re.S):
        label = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        out.append((m.group(1), label, False))
    return out


# ── высокоуровневое: собрать патч и создать заявку ────────────────────────────────
def _fmt_phone(digits):
    """База принимает телефон ТОЛЬКО в маске «+7 XXX-XXX-XXXX» (та же, что в расширении:
    offscreen.js) — сырые цифры валятся валидацией. На входе любые 10-11 цифр."""
    d = "".join(ch for ch in str(digits or "") if ch.isdigit())
    if len(d) == 11 and d[0] in "78":
        d = d[1:]
    if len(d) != 10:
        return str(digits or "")
    return "+7 %s-%s-%s" % (d[:3], d[3:6], d[6:])


def build_patch(form, lead, appliance_options=None, direction=None):
    """Патч формы из данных диалога. lead: словарь собранных полей (см. server).
    direction (bt|kp|mnc) нужен полям, живущим не во всех формах (is_far_ride — только КП)."""
    patch = {}
    городá = form.selects.get("CustomerRequest[city_id]") or []
    city_id = pick_option(городá, lead.get("city") or "")
    if city_id:
        patch["CustomerRequest[city_id]"] = city_id
    patch["Customer[phone]"] = _fmt_phone(lead.get("phone"))
    # «Тип» — обязательное поле без дефолта (без него серверный путь валится валидацией).
    # Правило владельца 16.08: тип решает история клиента В БАЗЕ — её видит только расширение
    # (оно вправе переставить на «Повтор»); сервер ставит «Гарантия» по словам клиента, иначе
    # «Впервые» (эталонные карточки владельца: во всех трёх — «Впервые»).
    patch["CustomerRequest[type]"] = "30" if lead.get("repeat_hint") == "гарантия" else "10"
    if direction == "kp" and "CustomerRequest[is_far_ride]" in form.fields:
        # обязательный селект КП с пустым дефолтом «Выбрать»: без явного «0=Нет» заявка
        # не проходит валидацию; «Дальний выезд» на спутники не ставим (решение В-008)
        patch.setdefault("CustomerRequest[is_far_ride]", "0")
    if lead.get("name"):
        patch["Customer[first_name]"] = lead["name"]
    if lead.get("address"):
        patch["Customer[address_street]"] = lead["address"]
    # ⚠ НАСЕЛЁННЫЙ ПУНКТ — СВОЁ ПОЛЕ («Нас.пункт»), а не часть улицы. Для села, деревни и
    # СНТ по нему филиал понимает, что ехать за черту. Имя поля ищем среди настоящих полей
    # формы: угадывать нельзя, поставим значение неизвестно куда.
    _нп = ((lead.get("addr") or {}).get("settlement") or "").strip()
    if _нп:
        _поле = next((n for n in form.fields
                      if n and re.search(r"settlement|locality", n, re.IGNORECASE)), "")
        if _поле:
            patch[_поле] = _нп
    # вид работ: опции зависят от города и подгружаются отдельно (appliance_options);
    # подбираем по теме, не нашли — «Прочая» (регламент: принимается по согласованию)
    виды = appliance_options or form.selects.get("CustomerRequest[appliance_type_id]") or []
    # ⚠ ТЕМА ПРАЙСА И КАТЕГОРИЯ CRM — РАЗНЫЕ СЛОВАРИ (этап 6б программы обучения).
    # pick_option ищет ПОДСТРОКУ, а справочник CRM говорит категориями: «смеситель» в
    # «Сантехнике» не содержится, «розетка» в «Электрике» тоже — и заявка уезжала с видом
    # работ «Прочая». Родственная беда уже была в боевой очереди: «вид работ не подобран:
    # опций в подгрузке 0, темы: стиральн». Поэтому к темам добавляем их КАТЕГОРИИ, а
    # сами темы пробуем первыми: если в справочнике города есть точная позиция, выиграет она.
    # ⚠ 25.08: список примет переехал в price_book.КАТЕГОРИИ — им же пользуется запасной
    # подбор тем в server._maybe_autocreate. Пока список жил здесь приватно, сервер о нём
    # не знал и клал в темы первые попавшиеся длинные слова реплики.
    # сервер уже раскрыл категории в lead["topics"]; повтор идемпотентен и нужен для
    # старых записей очереди, где раскрытия ещё нет
    _темы = price_book.with_categories(lead.get("topics") or [])
    _синонимы = [в for т in _темы for в in синонимы_темы(т)]
    ap = (pick_option(виды, *_темы)
          or (pick_option(виды, *_синонимы) if _синонимы else "")
          or pick_option(виды, "проч"))
    if ap:
        patch["CustomerRequest[appliance_type_id]"] = ap
    elif виды:
        # ⚠ МОЛЧА ПУСТЫМ НЕ ОСТАВЛЯЕМ. «Вид работ» — обязательное поле карточки; заявка
        # №1234567 уехала с пустым, и диспетчер узнал об этом, только открыв её руками.
        print("[ЗАЯВКА] вид работ не подобран: опций %d, темы: %s"
              % (len(виды), ", ".join(_темы) or "нет"), flush=True)
    партнёры = form.selects.get("CustomerRequest[partner_id]") or []
    pn = (lead.get("partner") or "").lstrip("0")
    if pn:
        pid = pick_number(партнёры, pn)
        if pid:
            patch["CustomerRequest[partner_id]"] = pid
        else:
            # ⚠ НЕ ПОДБИРАТЬ ПОХОЖЕГО. Партнёр решает, кому идут деньги за лид; чужой
            # номер в карточке хуже пустого поля — пустое диспетчер увидит и заполнит,
            # подставленное примет за правду. Молчать тоже нельзя: пишем в журнал.
            print("[ЗАЯВКА] партнёр %s не найден в справочнике (%d опций) — поле оставлено пустым"
                  % (pn, len(партнёры)), flush=True)
    if lead.get("comment"):
        patch["CustomerRequest[comments]"] = lead["comment"]
    if lead.get("review"):
        patch["CustomerRequest[is_req_fback]"] = "1"       # «Отзыв» (регламент п.2)
    if lead.get("call_before_visit"):
        patch["CustomerRequest[is_need_call_before_visit]"] = "1"
    # ⚠ «ПРОЗВОН» СЕРВЕРНЫЙ ПУТЬ ТЕРЯЛ (аудит 26.08). Флаг считался в server._maybe_autocreate
    # с 18.08 и доезжал только до браузерного расширения (offscreen.js): здесь его просто
    # не записывали. Заявка без договорённости по времени уходила в лид-центр как обычная,
    # и диспетчер не знал, что клиенту надо звонить, а не выезжать.
    if lead.get("call_check"):
        patch["CustomerRequest[is_need_call_check]"] = "1"
    # ⚠ ДОМ, КВАРТИРА, ПОДЪЕЗД, ЭТАЖ И ДАТА ВИЗИТА — ТОЖЕ ТОЛЬКО У РАСШИРЕНИЯ. Разбор
    # адреса (`_parse_addr`) отдаёт их с 25.08, а серверная форма клала в карточку одну
    # улицу: мастер получал «Горная» без номера дома. Ставим ровно те поля, что есть в
    # ЭТОЙ форме, — выдуманное имя ушло бы POST-ом неизвестно куда.
    адр = lead.get("addr") or {}
    for имя, значение in (("Customer[address_building]", адр.get("building")),
                          ("Customer[address_office]", адр.get("office")),
                          ("Customer[address_entrance]", адр.get("entrance")),
                          ("Customer[address_floor]", адр.get("floor"))):
        if значение and имя in form.fields:
            patch[имя] = str(значение)
    if адр.get("intercom") and "Customer[address_has_intercom]" in form.fields:
        patch["Customer[address_has_intercom]"] = "1"
    # ⚠ УЛИЦА — ИЗ РАЗБОРА, А НЕ СЫРАЯ СТРОКА (правка владельца 27.08: «неверно расписал адрес»).
    # Выше стояло `patch["Customer[address_street]"] = lead["address"]`, и в поле «Улица» уезжало
    # «Сосновка, центр.. Ул. Садовая, 12, ориентир Сбер.» целиком — при том, что разбор уже отдал
    # улицу, дом и населённый пункт по отдельности. Расширение брало `addr.street || lead.address`,
    # сервер — только сырую строку: один разбор, два потребителя, две разные формулы.
    if адр.get("street"):
        patch["Customer[address_street]"] = адр["street"]
    # ⚠ ИСТОЧНИК КАНАЛА — В «КОММЕНТАРИЙ ПАРТНЁРА» (владелец 27.08: «ты не указал партнёра»).
    if lead.get("partner_note") and "CustomerRequest[partner_comment]" in form.fields:
        patch["CustomerRequest[partner_comment]"] = lead["partner_note"]
    # Дата и время: у полной заявки это визит, у заявки на прозвон — когда набрать
    # (галочка «Прозвон» уже стоит выше, и вместе они читаются однозначно).
    if lead.get("visit_date") and "CustomerRequest[opened_at]" in form.fields:
        patch["CustomerRequest[opened_at]"] = lead["visit_date"]
    if lead.get("visit_time") and "cr_opened_at_time" in form.fields:
        patch["cr_opened_at_time"] = lead["visit_time"]
    return patch


def serialize(form, patch):
    """Вся форма + наш патч (порядок Хаба: дефолты не теряются)."""
    data = {}
    for name, f in form.fields.items():
        if not name:
            continue
        data[name] = f["value"]
    data.update(patch)
    data["save_close"] = "1"
    return data


def create_request(src, lead, dry=True):
    """(ok, id|None, отчёт). dry=True — всё, кроме POST."""
    html = _ensure_session(src)
    form = parse_form(html)
    if not form.fields:
        return False, None, {"error": "форма создания не найдена"}
    городá = form.selects.get("CustomerRequest[city_id]") or []
    city_id = pick_option(городá, lead.get("city") or "")
    appl = load_appliance_options(src, city_id) if city_id else []
    patch = build_patch(form, lead, appliance_options=appl, direction=src)
    missing = [k for k in ("CustomerRequest[city_id]", "Customer[phone]",
                           "CustomerRequest[appliance_type_id]")
               if not patch.get(k) and not form.fields.get(k, {}).get("value")]
    report = {"patch": {k: v for k, v in patch.items()}, "missing": missing, "dry": dry}
    if missing:
        return False, None, report
    if dry:
        return True, None, report
    body = serialize(form, patch)
    resp, final_url = _post(src, CREATE_PATH, body)
    errors = form_errors(resp)
    new_id = None
    m = re.search(r"[?&]id=(\d+)", final_url or "")
    if m:
        new_id = int(m.group(1))
    ok = bool(new_id) or ("customer-request/index" in (final_url or "") and not errors)
    report.update({"errors": errors[:5], "final_url": final_url, "id": new_id})
    return ok, new_id, report


def search_customer(src, phone):
    """Поиск клиента по телефону (для проверки активной заявки). [] при беде."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 10:
        return []
    try:
        _ensure_session(src)
        html, _u = _get(src, SEARCH_PATH + "?" + urllib.parse.urlencode({"q": digits[-10:]}))
        data = json.loads(html)
        items = data.get("results") or data.get("items") or data or []
        return items if isinstance(items, list) else []
    except Exception:
        return []
