/* Создание заявки в лид-центре — механика «Заявок Хаба»:
   GET формы → патч только наших полей → сериализация ВСЕЙ формы → POST save_close.
   Работает рабочими cookie-сессиями (credentials: include). */

const BASES = {
  bt: 'https://bt-lead-centre.ru',
  kp: 'https://kp-lead-centre.ru',
  mnc: 'https://mnc-lead-centre.ru',
};
const CREATE = '/admin/domain/customer-request/create';

chrome.runtime.onMessage.addListener((msg, _sender, respond) => {
  if (msg?.kind === 'crm-status') {
    // ⚠ ОПРОС СТАТУСОВ НЕ ДОЛЖЕН МЕШАТЬ СОЗДАНИЮ ЗАЯВОК. Заявки — деньги сейчас,
    // статусы — знание на потом: идём по одной карточке и молча пропускаем сбои.
    (async () => {
      const итог = [];
      for (const з of (msg.items || []).slice(0, 20)) {
        итог.push(await readStatus(BASES[з.dir], з.id));
      }
      return { ok: true, статусы: итог };
    })().then(respond).catch(e => respond({ ok: false, error: String(e.message || e) }));
    return true;
  }
  if (msg?.kind !== 'crm-create') return;
  // исключение здесь — почти всегда сеть/доступность CRM, а не форма:
  // помечаем retry, чтобы лид не хоронился в failed навсегда
  createRequest(msg.item).then(respond).catch(e => respond({ ok: false, retry: true, error: String(e.message || e) }));
  return true;
});

const norm = s => (s || '').toLowerCase().replace(/ё/g, 'е').trim();

/* ⚠ ГРАНИЦА СЛОВА, А НЕ ПРОСТО ПОДСТРОКА (26.08). Тема «кран» находилась внутри
   подписи «Замена ЭКРАНА», и заявка на телевизор уезжала с сантехническим видом работ.
   Тот же класс, что «7» внутри «_197» у партнёра ниже. Хвост не трогаем: темы это
   ОСНОВЫ и обязаны брать окончания подписи («стиральн» → «Стиральные машины»).
   Точно такое же правило стоит на сервере (crm.pick_option): путей создания два. */
function начинаетсяСоСлова(текст, основа) {
  let i = текст.indexOf(основа);
  while (i >= 0) {
    const слева = i > 0 ? текст[i - 1] : '';
    if (!слева || !/[a-zа-яё]/.test(слева)) return true;
    i = текст.indexOf(основа, i + 1);
  }
  return false;
}

/* ⚠ КЛИЕНТ И СПРАВОЧНИК CRM ГОВОРЯТ РАЗНЫМИ СЛОВАМИ (28.08, заявка ТЕСТ5 №1234568).
   Клиент пишет «телевизор», а в справочнике КП Москвы стоит «ЖК ТВ/Плазменные ТВ/
   Кинескопные ТВ» — слова «телевизор» там нет вовсе. Подбор не находил ничего и
   уходил в запасную «** Прочая», то есть в позицию «этим мы не занимаемся, только
   по согласованию». Заявка на обычный ремонт телевизора выглядела как несогласованная.

   Список составлен по НАСТОЯЩЕМУ справочнику, приехавшему от расширения, а не по
   догадкам: 10 позиций КП Москвы. Синонимы пробуются ПОСЛЕ самой темы и ДО «прочей»:
   своё имя всегда сильнее подсказки. Лишний синоним безвреден — если такой подписи
   в справочнике нет, поиск просто идёт дальше. */
const СИНОНИМЫ = [
  [/^телевизор|^телек|^тв$|плазм|кинескоп/i, ['жк тв', 'тв']],
  [/^ноутбук|^компьютер|^пк$|моноблок|систем.?ник|материнск/i,
   ['компьютер', 'ноутбук', 'моноблок']],
  [/^принтер|^мфу|печат|картридж/i, ['принтер', 'мфу']],
  [/^смартфон|^телефон|^айфон|^планшет/i, ['смартфон', 'планшет']],
  [/^роутер|вай.?фай|wi.?fi|^интернет/i, ['роутер', 'обжим']],
  [/^монитор|видеокарт/i, ['монитор', 'видеокарт']],
  [/пристав|playstation|^ps[0-9]?$|xbox|nintendo|steam/i,
   ['playstation', 'xbox', 'nintendo', 'steam']],
  [/жестк.?диск|жёстк.?диск|флеш|восстанов.*(информ|данн)|^ssd$|^hdd$/i,
   ['восстановление информации', 'восстановление']],
];

function синонимыТемы(тема) {
  const t = norm(тема);
  if (!t) return [];
  for (const [rx, варианты] of СИНОНИМЫ) if (rx.test(t)) return варианты;
  return [];
}

function pickOption(select, ...wanted) {
  if (!select) return '';
  for (const w of wanted) {
    const wl = norm(w);
    if (!wl) continue;
    for (const o of select.options || select.querySelectorAll('option')) {
      if (o.value && начинаетсяСоСлова(norm(o.textContent), wl)) return o.value;
    }
  }
  return '';
}

/* Какой список видов работ пускать в дело: подгрузку или тот, что форма уже показала.
   ⚠ ЧИСТАЯ ФУНКЦИЯ РАДИ ПРОВЕРКИ. Живой CRM отсюда не виден (доступ только с офисного
   ПК), и раньше этот выбор проверялся чтением исходника — а такая проверка в этой же
   работе дважды находила совпадение в СОСЕДНЕМ месте и молчала на диверсии. Вынесено
   отдельно, чтобы гонять настоящим прогоном на подставных списках. */
/* Сколько в списке ГОДНЫХ видов работ. Годная опция — та, у которой есть value:
   заглушку «Выбрать ...» в карточку не поставишь, и считать её списком нельзя.
   ⚠ Отдельной функцией РАДИ ПРОВЕРКИ: пока подсчёт жил внутри вызывающего кода,
   диверсия «считать любые опции» проходила молча — тест до него не доставал. */
function годныхОпций(sel) {
  if (!sel || !sel.querySelectorAll) return 0;
  return [...sel.querySelectorAll('option')].filter((o) => o.value).length;
}

function выбратьСписокВидов(подгрузка, годныхВПодгрузке, формаHtml, годныхВФорме) {
  if (годныхВПодгрузке >= 1) return { html: подгрузка, изФормы: false };
  if (годныхВФорме >= 1) return { html: формаHtml || '', изФормы: true };
  return { html: подгрузка || '', изФормы: false };
}

/* Опция, ЧИСЛО которой равно заданному — для справочников, где значение это номер.
   ⚠ ПОДСТРОКОЙ НОМЕР ИСКАТЬ НЕЛЬЗЯ (жалоба владельца на заявку №1234564: партнёр 007
   уехал в карточку как «_197»). Искали через pickOption подстрокой «7», и «7» честно
   нашлась внутри «_197» — первой же по списку. Обрамление пробелами не спасало: norm()
   делает .trim() и превращает « 7 » обратно в «7». Сравниваем числа: из ярлыка берём
   цифры, ведущие нули убираем с обеих сторон — «007» = «7», а «_197» = «197» ≠ «7».
   Тот же разбор и та же починка есть на сервере (crm.pick_number): путей создания два,
   серверный и через расширение, и правило обязано работать на обоих. */
function pickNumber(select, номер) {
  if (!select) return '';
  const n = String(номер || '').replace(/\D/g, '').replace(/^0+/, '');
  if (!n) return '';
  for (const o of select.options || select.querySelectorAll('option')) {
    if (!o.value) continue;
    const ц = (o.textContent || '').replace(/\D/g, '').replace(/^0+/, '');
    if (ц && ц === n) return o.value;
  }
  return '';
}

async function fetchDoc(url) {
  const r = await fetch(url, { credentials: 'include' });
  const text = await r.text();
  const doc = new DOMParser().parseFromString(text, 'text/html');
  doc.__finalUrl = r.url;
  if (doc.querySelector('input[type="password"], [name^="LoginForm["]')) {
    throw new Error('Сессия в CRM истекла — войдите на сайт лид-центра');
  }
  return doc;
}

function setVal(form, name, value) {
  let el = form.elements[name];
  if (!el) return false;
  // Yii шлёт СКРЫТЫЙ input + checkbox с одним именем: form.elements[name] тогда
  // коллекция, и присвоение value уходило в пустоту — «Отзыв» не вставал
  if (el.length !== undefined && el.tagName === undefined) {
    el = [...el].find(x => x.type === 'checkbox' || x.type === 'radio') || el[el.length - 1];
  }
  if (el.type === 'checkbox') el.checked = value === '1' || value === true;
  else el.value = value;
  return true;
}

function serializeForm(form) {
  const body = new URLSearchParams();
  for (const el of form.elements) {
    if (!el.name || el.disabled) continue;
    if (el.type === 'checkbox' || el.type === 'radio') {
      if (el.checked) body.append(el.name, el.value || '1');
      // Yii-пара: скрытый input с тем же именем уже прошёл отдельным элементом
    } else if (el.tagName === 'SELECT' && el.multiple) {
      for (const o of el.selectedOptions) body.append(el.name, o.value);
    } else {
      body.append(el.name, el.value ?? '');
    }
  }
  return body;
}

function formErrors(doc) {
  const out = [];
  for (const fb of doc.querySelectorAll('.invalid-feedback')) {
    const t = fb.textContent.trim();
    if (t) out.push(t);
  }
  for (const a of doc.querySelectorAll('.alert-danger, .alert-error')) {
    const t = a.textContent.trim();
    if (t) out.push(t.slice(0, 200));
  }
  return out;
}

async function findCustomerId(base, phone) {
  // клиент с таким телефоном уже есть в базе → заявку надо ПРИВЯЗАТЬ к нему
  // (CustomerRequest[customer_id]), иначе CRM отвечает «Телефон уже занят».
  // Пробуем оба формата номера (полный и последние 10 цифр) — как ищут руками.
  const full = String(phone || '').replace(/\D/g, '');
  const p10 = full.slice(-10);
  const masked = '+7 ' + p10.slice(0, 3) + '-' + p10.slice(3, 6) + '-' + p10.slice(6);
  const cands = [...new Set([masked, p10, full, '8' + p10])].filter(q => q.replace(/\D/g, '').length >= 5);
  const tried = [];
  for (const q of cands) {
    try {
      const r = await fetch(base + '/api/customer/search?q=' + encodeURIComponent(q) + '&_serverKey=', {
        credentials: 'include',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });
      const raw = await r.text();
      let d = null;
      try { d = JSON.parse(raw); } catch (_e) { tried.push(q + '→не-JSON'); continue; }
      const items = d?.results || d?.items || (Array.isArray(d) ? d : []);
      tried.push(q + '→' + items.length);
      if (items.length) {
        return { id: String(items[0].id ?? items[0].value ?? ''), count: items.length, tried };
      }
    } catch (e) {
      tried.push(q + '→' + (e.message || 'ошибка'));
    }
  }
  return { id: '', count: 0, tried };
}

async function hasClosedRequest(base, custId) {
  // Правило владельца 16.08: «Повтор» ставится ВСЕГДА, когда у клиента есть
  // хоть одна отработанная заявка («Готово» или «Отказ») — без срока
  // давности, мастер и время не важны. Отмены (КЦ/Филиал/Не оформлена)
  // повтором не считаются — работы с клиентом не было.
  try {
    const doc = await fetchDoc(base + '/admin/domain/customer/update?id=' + custId);
    for (const tr of doc.querySelectorAll('tr')) {
      if (!tr.querySelector('a[href*="customer-request/update?id="]')) continue;
      // статус — короткой ОТДЕЛЬНОЙ ячейкой: по всей строке ловились
      // комментарии вида «клиент отказался от смс» → ложный «Повтор»
      for (const td of tr.querySelectorAll('td')) {
        const t = (td.textContent || '').trim();
        if (t.length <= 30 && /^(?:Готово|Выполнен|Отказ)/i.test(t)) return true;
      }
    }
  } catch (_e) { /* не смогли прочитать историю — считаем «Впервые» */ }
  return false;
}

/* Статус ЖИВОЙ заявки клиента, если такая есть, иначе ''.
   Живая = ещё не отработана и не отменена: по такой мастер либо поедет, либо уже
   едет, и вторая заявка на того же клиента — дубль (жалоба владельца 25.08).
   ⚠ СПИСОК ЖИВЫХ СТАТУСОВ ПЕРЕЧИСЛЕН ЯВНО, а не «всё, что не Готово/Отказ»: в
   карточке встречаются отменённые («Отмена КЦ», «Отмена Филиал», «Не оформлена»),
   и по ним ехать некому — они создание НЕ блокируют. Ошибиться дешевле в сторону
   «создать»: пропущенный дубль диспетчер увидит, а не созданный лид пропадёт молча. */
const ЖИВЫЕ_СТАТУСЫ = /^(?:Ожидает|В работе|Назначен|Принят|Подтвержд|Едет|Согласован)/i;

async function openRequestStatus(base, custId) {
  try {
    const doc = await fetchDoc(base + '/admin/domain/customer/update?id=' + custId);
    for (const tr of doc.querySelectorAll('tr')) {
      if (!tr.querySelector('a[href*="customer-request/update?id="]')) continue;
      for (const td of tr.querySelectorAll('td')) {
        const t = (td.textContent || '').trim();
        // тот же приём, что в hasClosedRequest: статус — короткая ОТДЕЛЬНАЯ ячейка,
        // иначе в неё попадают комментарии диспетчера со словом «ожидает»
        if (t.length <= 30 && ЖИВЫЕ_СТАТУСЫ.test(t)) return t;
      }
    }
  } catch (_e) { /* карточка не прочиталась — не блокируем создание */ }
  return '';
}

async function recoverRequestId(base, custId) {
  // Yii иногда шлёт X-Redirect без id — тогда номер СОЗДАННОЙ заявки берём
  // из карточки клиента: свежайшая = наибольший id среди ссылок на заявки
  try {
    const doc = await fetchDoc(base + '/admin/domain/customer/update?id=' + custId);
    const ids = [...doc.querySelectorAll('a[href*="customer-request/update?id="]')]
      .map(a => Number(a.href.match(/[?&]id=(\d+)/)?.[1]) || 0);
    return ids.length ? Math.max(...ids) : null;
  } catch (_e) { return null; }
}

async function saveComments(base, id, partnerNote) {
  // «Комментарий партнёра» есть только в карточке — дозаписываем лёгким
  // сохранением _comments_only (механика «Заявок Хаба»), заявку не трогая
  try {
    const doc = await fetchDoc(base + '/admin/domain/customer-request/update?id=' + id);
    const param = doc.querySelector('meta[name="csrf-param"]')?.content || '_csrf-frontend';
    const token = doc.querySelector('meta[name="csrf-token"]')?.content || '';
    const body = new URLSearchParams();
    body.set(param, token);
    body.set('_comments_only', 'true');
    body.set('partner_comment', partnerNote);
    await fetch(base + '/admin/domain/customer-request/update?id=' + id, {
      method: 'POST', credentials: 'include', body,
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    });
  } catch (_e) { /* дозапись не должна валить созданную заявку */ }
}

/* ⚠ СТАТУС ЗАЯВКИ — ЭТО ДЕНЬГИ, А НЕ СПРАВКА (29.08, слова владельца). Заявка,
   закрытая в «Готов», «Отказ» или «Отмена Филиала», приносит 60 ₽; пока она
   «Ожидает» — тоже. А «Отмена КЦ» СПИСЫВАЕТ 60 ₽ обратно: такая заявка стоит вдвое
   дороже несозданной. До сих пор бот мерил переписку («дожал до адреса»), а не исход,
   и отличить выгодную заявку от убыточной было нечем.

   Имя поля в карточке нам неизвестно — живой CRM виден только с офисного ПК. Поэтому
   ищем ТРЕМЯ способами и докладываем, что сработало: тот же приём, что вытащил
   пустой «Вид работ». Не нашли — говорим прямо, а не выдумываем статус. */
const СТАТУСЫ = ['Ожидает', 'В пути', 'В работе СД', 'В работе', 'Отмена КЦ',
                 'Отмена Филиала', 'Отказ', 'Готов', 'Модерация', 'Не оформлена'];

async function readStatus(base, id) {
  const out = { id: id, статус: '', как: '', кандидаты: [] };
  try {
    const doc = await fetchDoc(base + '/admin/domain/customer-request/update?id=' + id);
    // 1. поле формы со статусом — самый надёжный источник
    const sel = [...doc.querySelectorAll('select, input')]
      .find((e) => /status|state|статус/i.test(e.name || e.id || ''));
    if (sel) {
      const v = sel.tagName === 'SELECT'
        ? (sel.options[sel.selectedIndex] || {}).textContent : sel.value;
      const t = (v || '').trim();
      if (t) { out.статус = t; out.как = 'поле ' + (sel.name || sel.id); }
    }
    // 2. заголовок карточки: там статус стоит подписью справа
    if (!out.статус) {
      const текст = (doc.body ? doc.body.textContent || '' : '');
      const найден = СТАТУСЫ.find((с) => текст.includes('Статус: ' + с)
                                      || текст.includes('Статус:' + с));
      if (найден) { out.статус = найден; out.как = 'подпись «Статус:»'; }
    }
    // 3. просто известное слово на странице — слабее всего, помечаем как догадку
    if (!out.статус) {
      const текст = (doc.body ? doc.body.textContent || '' : '');
      const все = СТАТУСЫ.filter((с) => текст.includes(с));
      out.кандидаты = все.slice(0, 5);
      if (все.length === 1) { out.статус = все[0]; out.как = 'единственное слово на странице'; }
      else out.как = все.length ? 'неоднозначно: ' + все.join(', ') : 'на странице статусов нет';
    }
  } catch (e) {
    out.как = 'карточка не открылась: ' + String(e.message || e).slice(0, 60);
  }
  return out;
}

async function createRequest(item) {
  const { dir, lead } = item;
  const base = BASES[dir];
  const warnings = [];
  /*
   * СПРАВОЧНИК CRM НАЗАД БОТУ (26.08.2026).
   *
   * Подбор «Вида работ» и «Вида услуг» до сих пор был догадкой: бот шлёт темы
   * («стиральн», «телевизор»), расширение ищет их ПОДСТРОКОЙ в списке опций, а какие
   * там опции на самом деле — не знает никто: живой CRM виден только с офисного ПК.
   * Не подошло ничего — ставится «Прочая» или вовсе первая попавшаяся строка.
   *
   * Расширение эти списки держит в руках каждый раз, когда создаёт заявку. Отдаём их
   * боту вместе с отчётом: по первому же живому созданию станет видно, как справочник
   * называет вещи, и подбор можно будет сделать по факту, а не по догадке.
   *
   * ⚠ ТОЛЬКО ПОДПИСИ И КОДЫ ОПЦИЙ. Ни телефона, ни адреса, ни имени клиента здесь нет:
   * это содержимое выпадающих списков, одинаковое для всех заявок города.
   */
  const справочник = { dir: dir, city: lead.city || '', вид_работ: [], вид_услуг: [] };
  let вид_работ = { id: '', text: '' };          // что реально выбрано в ЭТОЙ заявке
  if (!base) return { ok: false, error: 'неизвестное направление ' + dir };

  // существующий клиент: CRM создаёт ему заявку через create?customer_id=N —
  // ровно та ссылка, что стоит в карточке клиента. Форма приходит уже
  // привязанной, и валидация «Телефон уже занят» не срабатывает.
  const cust = await findCustomerId(base, lead.phone);
  if (cust.count > 1) {
    return { ok: false, error: 'в базе ' + cust.count + ' клиента с этим телефоном — создайте вручную' };
  }
  // ⚠ У КЛИЕНТА УЖЕ ЕСТЬ ЖИВАЯ ЗАЯВКА — НЕ СОЗДАЁМ ВТОРУЮ (жалоба владельца 25.08:
  // заявка создалась поверх висящей в «Ожидает»). Дедуп бота — только по своему
  // журналу «телефон+сутки»: заявку, заведённую человеком или вчера, он не видит,
  // потому что доступ к CRM есть только отсюда. Два филиала на одном клиенте — это
  // не удвоенный лид, а спор о том, кто едет, и в базе это выглядит накруткой.
  // Решение отдаём человеку: отказываем с понятной причиной, а лид остаётся в
  // очереди — его либо создадут руками, либо снимут кнопкой «Убрать».
  if (cust.id) {
    const живая = await openRequestStatus(base, cust.id);
    if (живая) {
      return { ok: false, skipped: true,
               error: 'у клиента уже есть заявка в статусе «' + живая + '» — вторую не создаю' };
    }
  }
  const createUrl = base + CREATE + (cust.id ? '?customer_id=' + cust.id : '');
  const doc = await fetchDoc(createUrl);
  const form = doc.querySelector('#customerRequestForm');
  if (!form) return { ok: false, error: 'форма создания не найдена' };

  /* ⚠ У ГОРОДА В CRM МОЖЕТ НЕ БЫТЬ СВОЕГО ИМЕНИ — ТОЛЬКО ИМЕНА ТЕРРИТОРИЙ, И ЭТО СТОИЛО
     ЧЕТЫРЁХ ЗАЯВОК ПО ПЕТЕРБУРГУ. В списке КП нет строки «Санкт-Петербург»: там «СПБ 1»…
     «СПБ 4» — четыре территории филиала. Бот присылал название города, pickOption не находил
     ничего, и заявка, собранная целиком, падала здесь. В журнале за 30 дней: СПб на КП — 4 раза,
     Рыбинск и Кисловодск на БТ — по разу (27.08).
     Какая именно территория — знает КАРТА: контур, в который попал адрес клиента, называется
     ровно «СПБ 4». Сервер кладёт это имя в lead.territory; здесь оно пробуется первым. */
  const citySel = form.elements['CustomerRequest[city_id]'];
  let cityId = lead.territory ? pickOption(citySel, lead.territory) : '';
  if (!cityId) cityId = pickOption(citySel, lead.city);
  if (!cityId) {
    /* ⚠ ОШИБКА ОБЯЗАНА НАЗЫВАТЬ ТО, ЧТО МЫ ВИДЕЛИ. Прежний текст сообщал только чего не нашли,
       и разгадывать, как город называется в CRM, приходилось вручную. Теперь список приезжает
       в журнал сам — следующий такой промах чинится одной строкой синонима. */
    const опции = Array.from(citySel ? citySel.options : [])
      .map(o => (o.textContent || '').trim()).filter(Boolean).slice(0, 40);
    return { ok: false, error: 'город «' + (lead.city || '—') + '»'
             + (lead.territory ? ' (территория «' + lead.territory + '»)' : '')
             + ' не найден в ' + dir + '. В списке CRM: ' + (опции.join(' | ') || 'пусто') };
  }
  setVal(form, 'CustomerRequest[city_id]', cityId);

  // «Вид работ» зависит от города — подгружаем тем же запросом, что нативная форма
  /* ⚠ ЗАПАСНОЙ ЗАХОД С has_partner=1 (28.08). Тестовая заявка №1234566 создалась с пустым
     «Видом работ», и то же предупреждение «опций в подгрузке 0» стоит в боевых записях
     очереди. Справочник видов зависит не только от города: у заявок с партнёром список
     свой. Один параметр — одна попытка, а стоит она заявки без направления работ.
     Диагностику дописываем сюда же: без неё «0 опций» не отличить от «CRM ответила
     ошибкой» и «сессия протухла». */
  const тянуть = async (hasPartner) => {
    const q = new URLSearchParams({ _get_appltype: 1, city_id: cityId,
                                    appl_id: '', subappl_id: '', has_partner: hasPartner });
    const r = await fetch(base + CREATE + '?' + q, { credentials: 'include' });
    return { html: await r.text(), status: r.status };
  };
  let ответ = await тянуть(0);
  let applHtml = ответ.html;
  if (!/<option/i.test(applHtml)) {
    const второй = await тянуть(1);
    if (/<option/i.test(второй.html)) { applHtml = второй.html; ответ = второй; }
    else ответ.диагноз = 'HTTP ' + ответ.status + ', ' + (applHtml || '').length
                         + ' симв., без <option>; с has_partner=1 — HTTP ' + второй.status
                         + ', ' + (второй.html || '').length + ' симв.';
  }
  /* ⚠ ТРЕТИЙ ЗАХОД — САМА ФОРМА (28.08). Оба AJAX-захода вернули пустоту на трёх заявках
     подряд: Белгород 26.08 и две московские 28.08 — все три уехали без «Вида работ».
     Но список видов стоит и в самой форме создания: подгрузка его только СУЖАЕТ под
     город. Пустая подгрузка — не повод отдавать заявку без направления работ: берём то,
     что форма уже показала, и говорим об этом в предупреждении. */
  /* ⚠ СЧИТАЕМ ГОДНЫЕ ОПЦИИ, А НЕ ЛЮБЫЕ (28.08, по заявке ТЕСТ3 №1234567). Первая
     редакция спрашивала «есть ли в ответе хоть один <option>» — и на боевой заявке
     это оказалось «да»: подгрузка вернула РОВНО ОДНУ пустую заглушку «Выберите».
     Проверка наличия прошла, третий заход не включился, поле осталось пустым при
     одиннадцати годных видах работ в самой форме. Годная опция — та, у которой есть
     value: заглушку выбора в карточку не поставишь. */
  const годных = годныхОпций;
  const _подгрSel = new DOMParser()
    .parseFromString('<select>' + (applHtml || '') + '</select>', 'text/html')
    .querySelector('select');
  const годныхВПодгрузке = годных(_подгрSel);
  const формаSel = form.elements['CustomerRequest[appliance_type_id]'];
  const годныхВФорме = годных(формаSel);
  const выбор = выбратьСписокВидов(applHtml, годныхВПодгрузке,
                                   формаSel ? формаSel.innerHTML : '', годныхВФорме);
  applHtml = выбор.html;
  const изФормы = выбор.изФормы;
  ответ.диагноз = (ответ.диагноз ? ответ.диагноз + '; ' : '')
                  + 'годных видов — в подгрузке: ' + годныхВПодгрузке
                  + ', в форме: ' + годныхВФорме;
  const applDoc = new DOMParser().parseFromString('<select>' + applHtml + '</select>', 'text/html');
  const applSel = applDoc.querySelector('select');
  let applId = '';
  for (const t of (lead.topics || [])) {
    applId = pickOption(applSel, t);
    if (applId) break;
  }
  // своё имя не нашлось — пробуем, как эту же вещь называет справочник
  if (!applId) {
    for (const t of (lead.topics || [])) {
      const вар = синонимыТемы(t);
      if (вар.length) applId = pickOption(applSel, ...вар);
      if (applId) break;
    }
  }
  справочник.вид_работ = applSel
    ? [...applSel.querySelectorAll('option')].filter((o) => o.value)
        .map((o) => ({ id: o.value, text: (o.textContent || '').trim() })).slice(0, 300)
    : [];
  if (!applId) applId = pickOption(applSel, 'проч');
  справочник.выбран_вид_работ = applId || '';
  /* ⚠ ВЫБРАННЫЙ ВИД — В ОТЧЁТ ПО КАЖДОЙ ЗАЯВКЕ, А НЕ ТОЛЬКО В СПРАВОЧНИК (28.08).
     Справочник пишется на сервере лишь когда ИЗМЕНИЛСЯ список опций, и после ТЕСТ6
     проверить, что выбралось, было нечем: в файле лежала запись от прошлой заявки.
     Результат подбора — свойство ЗАЯВКИ, а не справочника, и жить должен рядом с ней. */
  const _подпись = applId && applSel
    ? ([...applSel.querySelectorAll('option')].find((o) => o.value === applId) || {}).textContent
    : '';
  вид_работ = { id: applId || '', text: (_подпись || '').trim().slice(0, 80) };
  const sel = form.elements['CustomerRequest[appliance_type_id]'];
  if (applId && sel) {
    sel.innerHTML = applSel.innerHTML;
    sel.value = applId;
    if (sel.value !== applId) {          // вставка не прижилась — ставим опцию сами
      sel.append(new Option('вид работ', applId, true, true));
      sel.value = applId;
    }
  } else if (!applId) {
    const n = applSel ? applSel.querySelectorAll('option').length : -1;
    if (n > 1) {
      // опции есть, но ни тема, ни «Прочая» не подошли — берём первую непустую,
      // человек поправит в карточке (пустой вид хуже: заявка без направления работ)
      const first = [...applSel.querySelectorAll('option')].find(o => o.value);
      if (first && sel) {
        sel.append(new Option(first.textContent, first.value, true, true));
        sel.value = first.value;
      }
    }
    // n <= 1: у города в CRM нет видов работ (например, Test) — создаём без вида,
    // обязательность пусть решает сама CRM: №1234562 она так приняла
    // ⚠ Молча пустым не оставляем: заявка №1234564 уехала без «Вида работ», и узнал
    // об этом владелец, только открыв карточку. Причина была в темах — бот присылал
    // «добрый, сможете, отремонтировать» вместо «кровать»; чинится на его стороне,
    // но предупреждение нужно и здесь: справочники городов у CRM свои.
    /* ⚠ ПРЕДУПРЕЖДЕНИЕ ОБЯЗАНО РАЗЛИЧАТЬ ДВА ИСХОДА. Прежний текст был один и тот же
       и когда поле осталось ПУСТЫМ, и когда мы поставили первый вид из списка наугад.
       Для владельца это разные беды: пустое поле диспетчер увидит, а неверно
       заполненное — нет. */
    const n2 = годных(applSel);
    warnings.push('вид работ не подобран (темы: ' + ((lead.topics || []).join(', ') || 'нет') + ')'
                  + (n2 >= 1 ? ' — поставлен первый из списка, проверьте' : ' — ПОЛЕ ПУСТОЕ')
                  + (изФормы ? '; список взят из формы, подгрузка пуста' : '')
                  + (ответ && ответ.диагноз ? '; подгрузка: ' + ответ.диагноз : ''));
  }

  /*
   * «ВИД УСЛУГ» — ВТОРАЯ СТУПЕНЬ КАСКАДА (жалоба владельца 25.08: в его эталонной
   * заявке №1234565 стоит «Сантехника» → «Подключение бытовой техники», а бот
   * оставлял оба поля пустыми). Зависит от выбранного вида работ, поэтому и
   * подгружается ПОСЛЕ него, тем же запросом, но с заполненным appl_id.
   *
   * ⚠ ИМЯ ПОЛЯ НЕ УГАДЫВАЕТСЯ, А НАХОДИТСЯ В САМОЙ ФОРМЕ. Живой CRM отсюда не
   * виден (доступ только с офисного ПК), и вписать предполагаемое имя значило бы
   * поставить значение неизвестно куда. Ищем среди настоящих полей формы то, где
   * есть «subappl»; не нашли — НЕ трогаем ничего и пишем в предупреждения, какие
   * поля там есть на самом деле. Тогда первый же живой прогон скажет правду, а
   * создание заявки от этого не сломается.
   */
  if (applId) {
    const subName = [...form.elements]
      .map((e) => e.name)
      .find((n) => n && /subappl/i.test(n));
    if (!subName) {
      const селекты = [...form.elements]
        .filter((e) => e.tagName === 'SELECT' && e.name)
        .map((e) => e.name)
        .slice(0, 12);
      warnings.push('вид услуг: поля с «subappl» в форме нет; селекты: ' + селекты.join(', '));
    } else {
      try {
        const q2 = new URLSearchParams({ _get_appltype: 1, city_id: cityId,
                                         appl_id: applId, subappl_id: '', has_partner: 0 });
        const subHtml = await (await fetch(base + CREATE + '?' + q2,
                                           { credentials: 'include' })).text();
        const subDoc = new DOMParser().parseFromString('<select>' + subHtml + '</select>', 'text/html');
        const subSel = subDoc.querySelector('select');
        const опций = subSel ? [...subSel.querySelectorAll('option')].filter((o) => o.value) : [];
        справочник.вид_услуг = опций
          .map((o) => ({ id: o.value, text: (o.textContent || '').trim() })).slice(0, 300);
        // ⚠ ТОТ ЖЕ ОТВЕТ, ЧТО И НА ПЕРВОМ ШАГЕ = каскада здесь нет, это снова виды
        // работ. Ставить их во второе поле нельзя: получится «Сантехника →
        // Сантехника». Признак прямой — совпадение первого значения.
        const тоЖеСамое = опций.length && applSel
          && опций[0].value === ([...applSel.querySelectorAll('option')].find((o) => o.value) || {}).value;
        const цель = form.elements[subName];
        if (!опций.length || тоЖеСамое || !цель) {
          warnings.push('вид услуг не подобран: опций ' + опций.length
                        + (тоЖеСамое ? ' (пришли те же виды работ — нужен другой запрос)' : ''));
        } else {
          // Подходящую услугу выбираем по темам клиента, иначе первую: пустое поле
          // тут дороже неточного — филиал по нему решает, кого посылать.
          let subId = '';
          for (const t of (lead.topics || [])) {
            subId = pickOption(subSel, t);
            if (subId) break;
          }
          if (!subId) subId = опций[0].value;
          справочник.выбран_вид_услуг = subId || '';
          // ⚠ Опции переносим ЗНАЧЕНИЕМ И ТЕКСТОМ, а не разметкой. Строкой выше по
          // файлу у видов работ стоит `innerHTML = …innerHTML`, и это лишний риск:
          // в HTML из ответа могут приехать атрибуты-обработчики. Здесь нужен только
          // список пар «значение → подпись», и он собирается явно.
          цель.replaceChildren();
          for (const o of опций) цель.append(new Option(o.textContent, o.value));
          цель.value = subId;
          if (цель.value !== subId) {
            цель.append(new Option('вид услуг', subId, true, true));
            цель.value = subId;
          }
        }
      } catch (e) {
        warnings.push('вид услуг: подгрузка не удалась (' + String(e).slice(0, 60) + ')');
      }
    }
  }

  /* ⚠ СОМНЕНИЕ В АДРЕСЕ — В КАРТОЧКУ, А НЕ ТОЛЬКО В ЖУРНАЛ (28.08). Бот сверяет дом
     с картой и, если карта знает улицу, но не знает дома, кладёт сюда строку. Диспетчер
     читает комментарий заявки, а журнал бота — нет: без этого предупреждение не дошло бы
     до человека, который звонит клиенту. */
  const comment = [lead.addr_warn ? '⚠ АДРЕС ПОД ВОПРОСОМ: ' + lead.addr_warn : '',
                   lead.comment || ''].filter(Boolean).join('\n');
  if (lead.addr_warn) warnings.push('адрес: ' + lead.addr_warn);
  // телефон СТРОГО в формате маски базы «+7 999-999-9999» (слово владельца
  // 16.08: «по другому создать не нужно») — голые цифры база не принимает
  const d10 = String(lead.phone || '').replace(/\D/g, '').slice(-10);
  const phoneMasked = '+7 ' + d10.slice(0, 3) + '-' + d10.slice(3, 6) + '-' + d10.slice(6);
  const addr = lead.addr || {};
  if (cust.id) {
    setVal(form, 'CustomerRequest[customer_id]', cust.id);
  } else {
    setVal(form, 'Customer[phone]', phoneMasked);
    if (lead.name) setVal(form, 'Customer[first_name]', lead.name);
  }
  // адрес — по СВОИМ полям карточки (правка владельца 16.08), не одной строкой
  /*
   * НАСЕЛЁННЫЙ ПУНКТ — СВОЁ ПОЛЕ КАРТОЧКИ («Нас.пункт»). Раньше мы его не заполняли:
   * «село Супсех» и «деревня Клопузово» уезжали в «Улицу» целиком. Для города это
   * неважно, а для села, деревни и СНТ — важно: по этому полю филиал понимает, что
   * ехать ЗА ЧЕРТУ, и заявка идёт по согласованию.
   * ⚠ ИМЯ ПОЛЯ НЕ УГАДЫВАЕМ, а ищем в форме: живой CRM отсюда не виден. Не нашли —
   * ничего не трогаем и пишем предупреждение, как и с «Видом услуг».
   */
  if (addr.settlement) {
    const нп = [...form.elements].map((e) => e.name)
      .find((n) => n && /settlement|nas_?punkt|locality/i.test(n));
    if (нп) setVal(form, нп, addr.settlement);
    else warnings.push('нас.пункт «' + addr.settlement + '» некуда положить: поля нет в форме');
  }
  if (addr.street || lead.address) {
    /* ⚠ ГОВОРИМ, ЧТО ПОЛОЖИЛИ И КУДА (28.08, заявка ТЕСТ5 №1234568). У НОВОГО клиента
       весь адрес оказался одной строкой в «Улице», а «Дом», «Кв/офис», «подъезд» и
       «этаж» пустыми — при том что разобранный адрес в очереди правильный. У клиента
       СУЩЕСТВУЮЩЕГО (заявка №1234567) те же поля встали как надо, потому что CRM берёт
       адрес из его карточки. Разобрать причину чтением кода не вышло — значит пусть
       расширение само скажет, что оно клало и какие поля нашло. */
    const положено = {
      street: addr.street || lead.address || '', building: addr.building || '',
      office: addr.office || '', entrance: addr.entrance || '', floor: addr.floor || '',
    };
    const непринятые = [];
    for (const [ключ, знач] of Object.entries(положено)) {
      if (!setVal(form, 'Customer[address_' + ключ + ']', знач)) непринятые.push(ключ);
    }
    if (addr.intercom) setVal(form, 'Customer[address_has_intercom]', '1');
    if (непринятые.length) {
      warnings.push('поля адреса не найдены в форме: ' + непринятые.join(', ')
                    + '; клиент ' + (cust.id ? 'существующий' : 'НОВЫЙ'));
    } else if (!addr.street) {
      warnings.push('адрес не разобран — «Улица» получила строку целиком'
                    + ' (' + String(lead.address || '').slice(0, 60) + ')');
    }
  }
  // дата и время визита — из слота бота, по часам города клиента
  if (lead.visit_date) setVal(form, 'CustomerRequest[opened_at]', lead.visit_date);
  if (lead.visit_time) setVal(form, 'cr_opened_at_time', lead.visit_time);
  if (comment) setVal(form, 'CustomerRequest[comments]', comment);
  if (lead.partner_note) setVal(form, 'CustomerRequest[partner_comment]', lead.partner_note);
  if (lead.service_note) setVal(form, 'CustomerRequest[comments_service]', lead.service_note);

  // «Тип» обязателен, дефолта нет. Истина — БАЗА, не слова клиента:
  // есть закрытая заявка «Готово»/«Отказ» → «Повтор» (правило владельца);
  // слова о гарантии → «Гарантия»; всё прочее (и новый клиент) → «Впервые»
  const typeSel = form.elements['CustomerRequest[type]'];
  if (typeSel && !typeSel.value) {
    let хотим = 'Впервые';
    if (lead.repeat_hint === 'гарантия') хотим = 'Гарантия';
    else if (cust.id && await hasClosedRequest(base, cust.id)) хотим = 'Повтор';
    const тип = pickOption(typeSel, хотим)
      || { 'Гарантия': '30', 'Повтор': '20', 'Впервые': '10' }[хотим];
    setVal(form, 'CustomerRequest[type]', тип);
  }
  if (lead.review) setVal(form, 'CustomerRequest[is_req_fback]', '1');
  if (lead.call_before_visit) setVal(form, 'CustomerRequest[is_need_call_before_visit]', '1');
  // «Прозвон» (18.08): клиент просит звонок или не знает точного времени —
  // диспетчер созвонится и подберёт окно. Взаимоисключающая с «перед выездом»
  if (lead.call_check) setVal(form, 'CustomerRequest[is_need_call_check]', '1');
  const pn = String(lead.partner || '').replace(/^0+/, '');
  if (pn) {
    const pid = pickNumber(form.elements['CustomerRequest[partner_id]'], pn);
    if (pid) setVal(form, 'CustomerRequest[partner_id]', pid);
    // ⚠ ПОХОЖЕГО НЕ ПОДБИРАЕМ. Партнёр решает, кому идут деньги за лид: чужой номер
    // в карточке хуже пустого поля. Пустое диспетчер увидит, подставленное — примет
    // за правду. Возвращаем предупреждение наверх, в попап.
    else warnings.push('партнёр ' + pn + ' не найден в справочнике — поле пустое');
  }

  const body = serializeForm(form);
  body.set('save_close', '1');

  const resp = await fetch(createUrl, {
    method: 'POST', credentials: 'include', body,
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  });
  // Yii на AJAX-POST отвечает 302 + X-Redirect (без Location): это УСПЕХ
  // сохранения, а не ошибка — поймано уликами test-9 (статус=302, тела нет)
  const redirectTo = resp.headers.get('X-Redirect') || '';
  if (resp.status === 302 || redirectTo) {
    if (/default\/index|\/login/.test(redirectTo)) {
      return { ok: false, retry: true, error: 'сессия в CRM истекла — войдите на сайт лид-центра' };
    }
    let rid = Number(redirectTo.replace(/customer_id=\d+/, '')
                     .match(/[?&]id=(\d+)/)?.[1]) || null;
    if (!rid) {
      // Yii смолчал про id. Для нового клиента customer_id до создания не
      // существовал — ищем клиента по телефону ЗАНОВО (теперь он в базе)
      const c2 = cust.id ? cust : await findCustomerId(base, lead.phone);
      if (c2.id) rid = await recoverRequestId(base, c2.id);
    }
    if (rid && lead.partner_note) await saveComments(base, rid, lead.partner_note);
    return { ok: true, id: rid, errors: [], warnings, справочник, вид_работ,
             finalUrl: redirectTo || '(302 без адреса)' };
  }
  const text = await resp.text();
  const resDoc = new DOMParser().parseFromString(text, 'text/html');
  const errors = formErrors(resDoc);
  // id заявки: из redirect-URL (…update?id=N), НО не путать с нашим же
  // customer_id в адресе — поэтому вырезаем его перед поиском
  const cleanUrl = (resp.url || '').replace(/customer_id=\d+/, '');
  const id = Number(cleanUrl.match(/[?&]id=(\d+)/)?.[1]) || null;
  const savedByAlert = !!resDoc.querySelector('.alert-success');   // как looksSaved в Хабе
  const backToIndex = (resp.url || '').includes('customer-request/index');
  const ok = !!id || ((backToIndex || savedByAlert) && !errors.length);
  if (!ok && !cust.id && errors.some(e => /занято/i.test(e))) {
    errors.push('поиск клиента не нашёл его: ' + (cust.tried || []).join(', '));
  }
  if (!ok && !errors.length) {
    // ни успеха, ни ошибок — отдаём улики, по которым видно, что вернула CRM
    const formAgain = !!resDoc.querySelector('#customerRequestForm');
    const title = (resDoc.querySelector('title')?.textContent || '').trim().slice(0, 60);
    errors.push('ответ не распознан: url=' + (resp.url || '?') +
                ' | статус=' + resp.status + ' | форма-снова=' + (formAgain ? 'да' : 'нет') +
                ' | заголовок=' + title);
  }
  return { ok, id, errors: errors.slice(0, 6), warnings, справочник, вид_работ,
           finalUrl: resp.url };
}

/* Хвост для проверок: в браузере `module` не существует и ветка не выполняется,
   а node получает чистые функции без DOM и без chrome API. */
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { pickOption, pickNumber, начинаетсяСоСлова, norm,
                     выбратьСписокВидов, годныхОпций, синонимыТемы, СТАТУСЫ };
}
