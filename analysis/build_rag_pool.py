# -*- coding: utf-8 -*-
"""СБОРКА RAG-ПУЛА ЭТАЛОНОВ: пары «сообщение клиента → лучший ответ» для подсказки боту.

Источники (по убыванию веса):
  gold   — правки заказчика со страницы /review (все review_marks_round*.json + текущий
           review_marks.json: edit-тексты) — эталон; золото с позже введёнными запретами отсеивается.
  corpus — пары из УСПЕШНЫХ живых диалогов (телефон/запись состоялись), прошедшие чистку
           анти-маркерами (наберу перед выездом, только мне/мастеру, канцелярит, эхо, тире…).
Классы ситуаций — регексами. Дедуп похожих пар. Выход: brain/rag_examples.json.
Запуск:  py analysis\\build_rag_pool.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "brain"))
import paths  # noqa: E402  — единая точка правды по путям (переносимо между машинами)
import rag     # noqa: E402  — маска ПДн живёт там же, где выдача примеров
import sys, io, os, re, glob, json, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = paths.MERGED
OUT = os.path.join(os.path.dirname(HERE), "brain", "rag_examples.json")

RE_PHONE = re.compile(r"(?:\+?7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}")
RE_URL = re.compile(r"https?://\S+")

# анти-маркеры плохих ответов (наши зафиксированные запреты)
BAD_REPLY = re.compile(
    # ⚠ ОБА ПОРЯДКА СЛОВ: запрет заказчика — «наберу перед выездом», но живые пишут и
    # «перед выездом наберу». Прежний регекс ловил только первый вариант, и в пул годами
    # проходили запрещённые примеры (7 штук нашлось в действующем пуле 31.07).
    r"(?:наберу|позвоню|звоню|напишу)\s+(?:вас\s+|вам\s+|предварительно\s+)*перед\s+выездом|"
    r"перед\s+выездом[^.!?]{0,24}(?:набер|позвон|напишу|набрать|звоню)|"
    r"только\s+(?:мне|мастеру)|помощь\s+(?:ещё\s+|еще\s+)?(?:требуется|актуальна)|"
    # ВОПРОС О ГОРОДЕ палит бота (prompt.py: «⛔ ГОРОД НЕ СПРАШИВАЙ НИКОГДА — он известен из
    # объявления»). Пара «Когда Вы можете подъехать?» → «В каком городе проживаете?» выходила top-1.
    r"в\s+каком\s+городе|с\s+как(?:ого|ой)\s+город|из\s+какого\s+город|"
    r"какой\s+(?:у\s+вас\s+)?город\b|"
    r"вам\s+помощь\s+нужна|коллег|старш\w+\s+мастер|наш\s+специалист|вы\s+уже\s+думайте|"
    r"я\s+(?:же\s+)?вам\s+(?:уже\s+)?(?:написал|отписал|озвучил|сказал)|уточню\s+детали|стандартн\w+\s+вопрос|"
    r"так\s+не\s+счита|так\s+не\s+работаю|"
    # ЛИЧНАЯ НЕДОСТУПНОСТЬ МАСТЕРА (аудит 31.07). У бота такого состояния нет вообще: он всегда
    # принимает и тянет на ближайшее окно. В пуле лежали 6 живых отказов «не в городе / в отпуске /
    # пока что не работаю», и первый выходил top-1 на чистый первичный лид («сколько будет стоить
    # установить душевую кабину») — скопировав ход, бот хоронил готовую заявку.
    # ⚠ «СЕГОДНЯ уже не работаю, завтра к 11 удобно?» — ХОРОШИЙ пример (перенос на ближайший слот),
    # поэтому здесь только «сейчас/пока (что) не работаю», без «сегодня».
    r"не\s+в\s+городе|\bв\s+отпуск\w*|(?:сейчас|пока(?:\s+что)?)\s+не\s+работаю|"
    r"без\s+(?:вашего\s+)?соглас\w*\s+не\s+(?:сделаю|подниму|буду)|дороже\s+без\s+"
    r"сами\s+не\s+чините|\s—\s|заявк|оформ|диспетчер|оператор|цены\s+адекватн|точно\s+потянете|"
    r"по\s+телефону\s+не\s+скаж|^до\s+свидания|^всего\s+доброго", re.IGNORECASE)
BAD_CLIENT = re.compile(r"обманул|верните|жалоб|развод", re.IGNORECASE)

# ── БОЛЬШАЯ ВЫГРУЗКА (архив прошлого подрядчика, 39 940 диалогов от 31 аккаунта) ──
# Путь задаётся ключом --dump. Формат: jsonl.gz, строка = {partner, chat_id, messages[
# {ts, role: client|agent, text}]}. Стилистически это ЖИВЫЕ мастера (медиана 4 слова,
# тире 0%, опечатки), а не бот — проверено замером, поэтому учиться на них можно.
# ⚠ ДВА ФИЛЬТРА, ОБЯЗАТЕЛЬНЫХ ИМЕННО ДЛЯ ЭТОГО ИСТОЧНИКА:
#  1) РЕЧЬ ДИСПЕТЧЕРА. Часть аккаунтов пишет о мастере в третьем лице («мастер подъедет»,
#     «передал коллеге», «мы свяжемся»). Наш бот — мастер-одиночка, такие примеры сломали бы
#     легенду, за которую боролись 39 сессий. В выгрузке таких 0.5%.
#  2) ЧУЖИЕ ЦЕНЫ. Это 31 разный мастер со своими прайсами и городами; их суммы противоречат
#     нашему регламенту. Реплики с числами от 100 и выше не берём вовсе (6.7% выгрузки) —
#     нам нужна МАНЕРА отработки, а цифры бот берёт из плейбука.
_THIRD_PERSON = re.compile(
    r"\bмастер\w*\s+(?:при|под|за|по|св|вам|вас|вы|бу|см|ск|ул|уточ)|"
    r"(?:у|от|для|с)\s+мастер\w*|мастеру\b|наш\w*\s+мастер|передам\s+мастер|"
    r"мы\s+(?:свяж|перезвон|уточн|подъед|прие|сдела|отправ)|свяжемся|"
    r"наш\w*\s+(?:специалист|сотрудник|менеджер)|коллег", re.IGNORECASE)
_HAS_MONEY = re.compile(r"\d{3,}")
# Коммерческие УСЛОВИЯ чужих мастеров — та же беда, что и цены: у них выезд бесплатный, у нас
# по партнёрской сетке «выезд и диагностика 1000, при работах бесплатно» (кроме МНЧ). На сравнении
# 31.07 такой пример всплыл top-1 на «а если цена не устроит, платить за приезд?».
_FOREIGN_TERMS = re.compile(
    r"выезд\w*[^.!?]{0,20}бесплатн|бесплатн\w*[^.!?]{0,20}(?:выезд|диагностик)|"
    r"диагностик\w*[^.!?]{0,20}(?:бесплатн|платн)|гаранти\w*[^.!?]{0,20}(?:месяц|год|дн)",
    re.IGNORECASE)
#  3) КАПИТУЛЯЦИЯ. Первый сбор показал, что класс «возражение» из выгрузки — это сплошь
#     «Хорошо, спасибо» / «Хорошо, извините» / «жду от вас сообщения»: мастер сдаётся с первого
#     «подумаю». Наша стратегия (дерево если-то, сессия 8) — ОДНА попытка удержать с крючком.
#     700 примеров сдачи научили бы бота ровно обратному, поэтому такие ответы выбрасываем.
# ⚠ РАСШИРЕН ПОСЛЕ АУДИТА 31.07: прежняя версия требовала, чтобы слово капитуляции шло СРАЗУ за
# подтверждением, и пропускала самые частые формы — «хорошо напишите», «Хорошо, договорились»,
# «Хорошо, если помощь потребуется напишите». Из-за этого на канонических возражениях
# («Я подумаю», «Для меня дорого») в top-1 всплывал пример, где мастер СДАЁТСЯ.
_SURRENDER = re.compile(
    r"^(?:хорошо|ладно|понял|поняла|ясно|окей|ок|спасибо|здравствуйте)?\b[\s,.!]*"
    r"(?:спасибо|извин\w*|удачи|всего\s+доброго|до\s+свидания|на?пиш\w+|жду|буд(?:у|ем)\s+жда|"
    r"договорились|как\s+(?:решите|надумаете|будете\s+готовы)|обращайтесь|"
    r"если\s+(?:что|надо|нужно|понадоб|передумаете|надумаете|помощь|буд))"
    r"[^.!?]{0,40}$", re.IGNORECASE)
# ТОЛЬКО «возражение». Это единственный класс, где своих примеров мало (33) и где содержание —
# МАНЕРА удержания, а не коммерческие условия. Сравнение до/после 31.07 показало, почему нельзя
# брать остальные: из «выезда» всплыло «выезд по городу бесплатный» там, где у нас партнёрская
# формула с 1000, а из «формата» — размытое «все предоставлю» вместо конкретного срока гарантии.
# У 31 чужого мастера свои цены, гарантии и условия — в этих классах они прямо противоречат нашим.
_DUMP_CLASSES = {"возражение"}
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿]")


def _dialog_succeeded(msgs):
    """Тот же критерий, что и для нашего корпуса: телефон получен или запись состоялась."""
    blob = " ".join((m.get("text") or "") for m in msgs if m.get("role") == "client")
    booked = any("запис" in (m.get("text") or "").lower()
                 for m in msgs if m.get("role") != "client")
    return bool(RE_PHONE.search(blob) or booked)


def _arg(name):
    """Значение ключа командной строки вида --key ЗНАЧЕНИЕ."""
    a = sys.argv
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else ""


def collect_bigdump(path, limit_per_class=None):
    """Пары «клиент → мастер» из большой выгрузки, через все наши фильтры качества."""
    import gzip
    pairs = []
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            msgs = d.get("messages") or []
            if len(msgs) < 4 or not _dialog_succeeded(msgs):
                continue                              # берём только УСПЕШНЫЕ диалоги
            for i, m in enumerate(msgs[:-1]):
                if m.get("role") != "client":
                    continue
                t = (m.get("text") or "").strip()
                if len(t) < 8 or BAD_CLIENT.search(t):
                    continue
                ops = []
                j = i + 1
                while j < len(msgs) and msgs[j].get("role") != "client" and len(ops) < 2:
                    ops.append((msgs[j].get("text") or "").strip())
                    j += 1
                reply = "\n".join(x for x in ops if x)
                if not (4 <= len(reply) <= 260):
                    continue
                if BAD_REPLY.search(reply) or _THIRD_PERSON.search(reply):
                    continue
                if _HAS_MONEY.search(reply):          # чужой прайс — не наш
                    continue
                if _FOREIGN_TERMS.search(reply):      # чужие условия выезда/гарантии
                    continue
                if _SURRENDER.match(reply.strip()):   # сдался с первого «подумаю» — не учим этому
                    continue
                if _EMOJI.search(reply):              # эмодзи у нас запрещены полностью
                    continue
                if RE_PHONE.search(reply) or is_degenerate(reply):
                    continue
                cls = classify(t)
                if cls not in _DUMP_CLASSES:
                    continue
                reply = fix_known_slips(reply, t)
                if len(reply) < 4:
                    continue
                pairs.append({"q": norm(t), "a": norm_ответ(reply, 240), "class": cls,
                              "src": "dump", "key": d.get("chat_id") or ""})
    return pairs

# ── ПОЧИНКА ИЗВЕСТНЫХ ЛЯПОВ В ПРИМЕРАХ ───────────────────────────────────────────
# «Электрический или газовый?» законно только для бойлера/плиты/духовки/варочной/колонки.
# У холодильника, стиралки, посудомойки, СВЧ газового варианта НЕ БЫВАЕТ. В золоте r1-01
# (правка заказчика раунда 1) этот хвост прилип к холодильнику и, попадая в top-1 ретрива
# на КАЖДЫЙ холодильный лид, копировался моделью дословно — живая проба 30.07 это показала.
_NO_GAS_APPLIANCE = re.compile(r"холодильник|морозил|винн\w+\s*шкаф|стиральн|стиралк|посудомо|"
                               r"микроволн|\bсвч\b|кофемашин|пылесос", re.IGNORECASE)
_GAS_APPLIANCE = re.compile(r"бойлер|водонагрев|колонк|плит[аыуе]|духов|варочн|поверхност", re.IGNORECASE)
_GAS_Q_RX = re.compile(r"\s*(?:[.,]\s*)?(?:электрическ\w+\s+или\s+газов\w+|газов\w+\s+или\s+электрическ\w+)\s*\??",
                       re.IGNORECASE)


# «Выезд по городу бесплатный» — верно для МНЧ, но НЕ для техники: по регламенту приёма
# партнёрской заявки в КП и БТ действует формула «бесплатны при работах, иначе 1000».
# Золото r1-10 (посудомойка → «выезд по городу бесплатный») выходило top-1 на «сколько за
# приезд возьмёте?» и вдобавок ГЛУШИЛО код-корректор bt1000. Аудит 31.07.
_TECH_TOPIC_RX = re.compile(
    r"холодильник|морозил|стиральн|стиралк|посудомо|сушильн|духовк|варочн|электроплит|"
    r"микроволн|\bсвч\b|водонагрев|бойлер|кофемашин|телевизор|\bтв\b|ноутбук|компьютер|\bпк\b|"
    r"моноблок|принтер|\bмфу\b|роутер|видеокарт", re.IGNORECASE)
_FREE_VISIT_RX = re.compile(r"выезд\w*[^.!?]{0,25}бесплатн\w*|бесплатн\w*[^.!?]{0,25}выезд\w*",
                            re.IGNORECASE)
_PARTNER_VISIT = ("Выезд и диагностика бесплатны при выполнении работ, "
                  "а если от работ откажетесь, это 1000 рублей")


def fix_known_slips(reply, client_text=""):
    """Точечно чистит примеры от ляпов, которые модель копирует дословно."""
    r = reply or ""
    both = (r + " " + (client_text or ""))
    if _NO_GAS_APPLIANCE.search(both) and not _GAS_APPLIANCE.search(both):
        r = _GAS_Q_RX.sub("", r).strip()
    if _TECH_TOPIC_RX.search(both) and _FREE_VISIT_RX.search(r) and "1000" not in r:
        r = _FREE_VISIT_RX.sub(_PARTNER_VISIT, r, count=1)
    r = r.replace("через авто ", "через авито ")      # опечатка заказчика в правке r1-02
    r = re.sub(r"\bпоможем\b", "помогу", r)           # легенда одиночки: «мы» палит контору
    return re.sub(r"\s{2,}", " ", r).strip()

CLASSES = [
    ("вход-заявка",  re.compile(r"здравствуйте|добрый|нужн[оа]|сломал|не работает|не включается|теч[её]т|собрать", re.I), 0),
    ("цена",         re.compile(r"сколько|цен[аыу]|стоимост|стоит|почём|примерн|от и до|максимум|бюджет|дорого|дешевл", re.I), 1),
    ("выезд",        re.compile(r"выезд|приезд|вызов|диагностик", re.I), 2),
    ("время-слот",   re.compile(r"когда|во сколько|сегодня|завтра|срочно|сейчас|время", re.I), 3),
    ("данные",       re.compile(r"\bул\b|улиц|кв\.?\s?\d|подъезд|этаж|адрес|номер|телефон|\d{6,}", re.I), 4),
    ("возражение",   re.compile(r"подума|дорого|сравн|не устро|говорят|сомнева|не увер|посовет|друго\w+\s+мастер", re.I), 5),
    ("формат",       re.compile(r"привез|мастерск|позвон|созвон|ваш номер|наличи|запчаст|гаранти|оплат|перевод|карт[ае]", re.I), 6),
]


def classify(text):
    t = (text or "").lower()
    best = "прочее"
    for name, rx, prio in reversed(CLASSES):     # более специфичные классы позже в списке — берём их
        if rx.search(t):
            best = name
            break
    return best


def norm(t, cap=220):
    t = RE_URL.sub("🖼", t or "")
    t = RE_PHONE.sub("<номер>", t)
    return re.sub(r"\s+", " ", t).strip()[:cap]


def norm_ответ(t, cap=240):
    """То же, что norm, ПЛЮС выпрямление орфографии оператора.

    ⚠ ТОЛЬКО ДЛЯ ОТВЕТА, НИКОГДА ДЛЯ ВОПРОСА. Вопрос — речь КЛИЕНТА, и она обязана
    остаться такой, как люди пишут: по ней ищутся похожие ситуации, а «причесав» её мы
    перестанем находить живые обращения с опечатками.

    ⚠ ЗАЧЕМ (замер 24.08). Пул — это то, по чему бот равняет тон, и вместе с интонацией
    он перенимал ошибки: 74 ответа с «в течении» и НИ ОДНОГО противовеса, 21 «что бы»
    вместо «чтобы». Отбора по грамотности в петле не было ни на одном шаге. Правила живут
    в одном месте — analysis/fix_rag_orthography.py, там же разовый выпрямитель для
    действующего файла; второго списка того же смысла не заводим.
    """
    import fix_rag_orthography as _орф
    return _орф.выпрямить(norm(t, cap))[0]


# ПУСТЫШКИ: ответ без содержания («Здравствуйте», «Вы не ответили», «?») — в подсказке модели такой
# пример бесполезен, а иногда и вреден (пинг «Вы не ответили» вылезал в top-1 на вопрос о диагностике).
# Отбраковываем ПО СМЫСЛУ, а не по длине: короткие, но полезные («Записал вас🤝», «к 10-10:30») остаются.
_PING_RX = re.compile(r"^(вы\s+не\s+ответили|вы\s+тут|вы\s+здесь|ал+о|\?+)\W*$", re.IGNORECASE)
_USEFUL_RX = re.compile(r"\?|\d|запис|подъед|приед|адрес|номер|когда|удобно|сделаю|беру|помогу|"
                        r"не\s+занимаюсь|не\s+работаю", re.IGNORECASE)


def is_degenerate(reply):
    r = (reply or "").strip()
    if _PING_RX.match(r):
        return True
    return len(r) < 15 and not _USEFUL_RX.search(r)


def sig(client, reply):
    """Подпись для дедупа: стемы значимых слов."""
    w = re.findall(r"[а-яёa-z0-9]{4,}", (client + " " + reply).lower())
    return " ".join(sorted({x[:5] for x in w})[:18])


def collect_corpus():
    pairs = []
    # ⚠ GLOB БЕЗ СОРТИРОВКИ — ЭТО СЛУЧАЙНЫЙ ПОРЯДОК ФАЙЛОВ, а от порядка зависит, кто
    # переживёт дедуп: правило «выживает более ранний» без устойчивого порядка ничего не
    # значит. Замер: пересборка меняла 1237 пар из 4140. Пока это так, петля обучения
    # непроверяема — любой замер «до/после» меряет шум сборки, а не правку.
    # (Соседний источник, review_marks_round*, сортируется с самого начала — здесь забыли.)
    for fp in sorted(glob.glob(os.path.join(SRC, "*.json"))):
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        msgs = [m for m in (d.get("messages") or []) if (m.get("text") or "").strip()]
        if len(msgs) < 4:
            continue
        blob = " ".join((m.get("text") or "") for m in msgs if m.get("role") == "client")
        booked = any("запис" in (m.get("text") or "").lower() for m in msgs if m.get("role") == "operator")
        if not (RE_PHONE.search(blob) or booked):
            continue                                  # берём только УСПЕШНЫЕ диалоги
        for i, m in enumerate(msgs[:-1]):
            if m.get("role") != "client":
                continue
            t = m.get("text") or ""
            if len(t) < 8 or BAD_CLIENT.search(t):
                continue
            ops = []
            j = i + 1
            while j < len(msgs) and msgs[j].get("role") == "operator" and len(ops) < 2:
                ops.append(msgs[j].get("text") or "")
                j += 1
            if not ops:
                continue
            reply = "\n".join(ops)
            if not (4 <= len(reply) <= 260) or BAD_REPLY.search(reply):
                continue
            if RE_PHONE.search(reply) or is_degenerate(reply):
                continue
            reply = fix_known_slips(reply, t)
            if len(reply) < 4:
                continue
            pairs.append({"q": norm(t), "a": norm_ответ(reply, 240), "class": classify(t),
                          "src": "corpus", "key": d.get("conversation_key")})
    return pairs


def collect_gold():
    gold = []
    # архивные раунды (review_marks_roundN.json) + живой текущий раунд (review_marks.json)
    rounds = [(os.path.basename(p), os.path.basename(p).replace("review_marks_", "review_dialogs_"))
              for p in sorted(glob.glob(os.path.join(HERE, "review_marks_round*.json")))]
    rounds.append(("review_marks.json", "review_dialogs.json"))
    for marks_f, dial_f in rounds:
        mp, dp = os.path.join(HERE, marks_f), os.path.join(HERE, dial_f)
        if not (os.path.exists(mp) and os.path.exists(dp)):
            continue
        marks = json.load(open(mp, encoding="utf-8"))
        dials = {d["id"]: d for d in json.load(open(dp, encoding="utf-8")).get("dialogs", [])}
        for did, m in marks.items():
            d = dials.get(did)
            if not d:
                continue
            for idx_s, mm in (m.get("msgs") or {}).items():
                edit = (mm or {}).get("edit")
                if not edit:
                    continue
                try:
                    idx = int(idx_s)
                except ValueError:
                    continue
                ctx = ""
                for k in range(idx - 1, -1, -1):
                    if d["messages"][k]["role"] == "client":
                        ctx = d["messages"][k]["text"]
                        break
                if len(ctx) < 5 or len(edit) < 5:
                    continue
                if BAD_REPLY.search(edit):
                    # старая правка заказчика, содержащая ПОЗЖЕ введённый запрет («только мне» и т.п.)
                    print("  пропущено золото с устаревшим запретом: %r" % edit[:70])
                    continue
                edit = fix_known_slips(edit, ctx)
                if is_degenerate(edit):
                    continue
                gold.append({"q": norm(ctx), "a": norm_ответ(edit, 240), "class": classify(ctx),
                             "src": "gold", "key": did})
    return gold


def без_пдн(pairs):
    """Убирает телефоны и адреса из пар пула, сохраняя суть обращения и служебные поля.

    Маска берётся из rag — та же, что режет на выдаче. Своей копии здесь быть не
    должно: две маски однажды разъедутся, и разойдутся они молча.
    """
    очищенные = []
    for п in pairs or []:
        к = dict(п)
        for поле in ("q", "a"):
            if к.get(поле):
                к[поле] = rag.mask_pii(к[поле])
        очищенные.append(к)
    return очищенные


def main():
    # --dump ПУТЬ  — добавить большую выгрузку; --cap N — лимит на класс; --out ФАЙЛ — куда писать
    dump = _arg("--dump")
    cap = int(_arg("--cap") or 160)
    out_path = _arg("--out") or OUT

    gold = collect_gold()
    corpus = collect_corpus()
    extra = collect_bigdump(dump) if dump else []
    print("золото (правки заказчика): %d | корпус-кандидатов: %d | из выгрузки: %d"
          % (len(gold), len(corpus), len(extra)))
    seen, pool = set(), []
    # порядок важен: золото → наш корпус → чужая выгрузка. При дедупе выживает более ранний,
    # то есть наши материалы всегда вытесняют совпадающие из выгрузки.
    for p in gold + corpus + extra:
        s = sig(p["q"], p["a"])
        if s in seen:
            continue
        seen.add(s)
        pool.append(p)
    # балансировка: кап на класс, чтобы не задавили «вход-заявка»
    by = collections.defaultdict(list)
    for p in pool:
        by[p["class"]].append(p)
    # ⚠ И ПОРЯДОК КЛАССОВ ТОЖЕ ФИКСИРУЕМ. defaultdict хранит порядок вставки, а он
    # зависит от порядка пула — то есть от того же glob. Сортировка по имени класса
    # делает файл побайтно воспроизводимым при одинаковом входе.
    final = []
    for cls in sorted(by):
        final.extend(by[cls][:cap])
    # ⚠ ПДн ЧУЖИХ КЛИЕНТОВ ЧИСТЯТСЯ ЗДЕСЬ, А НЕ ТОЛЬКО В ГОТОВОМ ФАЙЛЕ (22.08).
    # Пул собирается из ЖИВЫХ диалогов, а note_for подставляет примеры в system
    # каждого хода. Один раз файл уже вычистили руками — и этого мало: пересборка
    # вернула бы и телефоны, и адреса, а заметил бы это только тот, кто снова
    # догадался бы прогнать test_batch4_privacy. Чистим на входе в файл.
    final = без_пдн(final)

    # ── СБОРКА НЕ ИМЕЕТ ПРАВА УНИЧТОЖИТЬ ПУЛ ────────────────────────────────────
    # ⚠ ЗАМЕР 25.08: пересборка давала 1132 пары против 4140 в боевом файле — то есть
    # СТЁРЛА БЫ 3027, три четверти. Терялось:
    #   · corpus 3786 → 1089 (исходных диалогов сборщик читает меньше, чем читал тогда);
    #   · dump 151 → 0 (чужая выгрузка подаётся ключом --dump, без него её нет);
    #   · «этап7:*» 160 — таких пар сборщик не умеет делать ВООБЩЕ, они заведены иначе.
    # Первые два — вопрос входных данных, третий — принципиальный. Пока это так, сборка
    # обязана быть ДОБАВЛЯЮЩЕЙ: берём всё, что уже есть, и дополняем свежим. Иначе один
    # запуск «просто пересобрать» уносит три четверти эталонов, и заметит это не тот, кто
    # запускал, а клиент — по ответам бота.
    прежние = []
    try:
        with open(out_path, encoding="utf-8") as f:
            прежние = (json.load(f) or {}).get("examples") or []
    except (OSError, ValueError):
        прежние = []
    if прежние:
        было_ключей = {sig(p.get("q", ""), p.get("a", "")) for p in final}
        добавлено = 0
        for p in прежние:
            k = sig(p.get("q", ""), p.get("a", ""))
            if k not in было_ключей:
                было_ключей.add(k)
                final.append(p)
                добавлено += 1
        print("⚠ сборка ДОБАВЛЯЮЩАЯ: своих пар %d, перенесено из прежнего пула %d"
              % (len(final) - добавлено, добавлено))
        if добавлено:
            по_ист = collections.Counter(p.get("src", "(нет)") for p in прежние
                                         if sig(p.get("q", ""), p.get("a", "")) not in
                                         {sig(x.get("q", ""), x.get("a", "")) for x in final[:len(final) - добавлено]})
            print("  сборщик не воспроизводит: %s"
                  % ", ".join("%s=%d" % kv for kv in по_ист.most_common(6)))

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"examples": final}, f, ensure_ascii=False, indent=1)
    print("итог пула: %d пар -> %s" % (len(final), out_path))
    for cls, items in sorted(by.items(), key=lambda x: -len(x[1])):
        take = items[:cap]
        g = sum(1 for x in take if x["src"] == "gold")
        c = sum(1 for x in take if x["src"] == "corpus")
        dmp = sum(1 for x in take if x["src"] == "dump")
        print("  %-14s %4d  (золото %d / наш корпус %d / выгрузка %d)" % (cls, len(take), g, c, dmp))


if __name__ == "__main__":
    main()
