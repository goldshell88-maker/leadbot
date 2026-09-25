/* Фон: опрос очереди лид-бота, бейдж, авто-режим через offscreen-документ. */

const DEFAULTS = {
  botUrl: 'https://72-56-68-159.sslip.io',
  token: '',
  mode: 'manual',          // manual: создаю по кнопке | auto: создаю сам
  intervalSec: 60,
  dirs: { kp: true, bt: true, mnc: true },
  // «Следить за ботом» (просьба владельца 30.08): расширение держит одну вкладку
  // LeadChat открытой на диалоге, который прямо сейчас ведёт бот.
  leadchatUrl: 'https://188-225-34-82.sslip.io',
  followBot: false,
};

async function cfg() {
  const st = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...st, dirs: { ...DEFAULTS.dirs, ...(st.dirs || {}) } };
}

chrome.runtime.onInstalled.addListener(() => schedule());
chrome.runtime.onStartup.addListener(() => schedule());

async function schedule() {
  const c = await cfg();
  chrome.alarms.clear('poll');
  chrome.alarms.create('poll', { periodInMinutes: Math.max(0.5, c.intervalSec / 60) });
  poll();
}

chrome.alarms.onAlarm.addListener(a => {
  if (a.name === 'poll') poll();
  // ⚠ ОТДЕЛЬНЫЙ БУДИЛЬНИК, А НЕ ХВОСТ К ОПРОСУ ОЧЕРЕДИ. Очередь опрашивается каждые
  // полминуты — статусы столько раз читать незачем, а CRM это чужая рабочая сессия.
  if (a.name === 'status') опроситьСтатусы();
  if (a.name === 'follow') followBotNow(false);
});
chrome.alarms.create('status', { periodInMinutes: 30, delayInMinutes: 3 });
// ⚠ только sync (настройки): poll() пишет результаты в storage.local, и без
// фильтра каждая запись перепланировала опрос — расширение молотило бота
// каждые 2 секунды вместо минуты (поймано на первом же подключении 16.08)
chrome.storage.onChanged.addListener((_ch, area) => { if (area === 'sync') schedule(); });

async function api(path, body) {
  const c = await cfg();
  if (!c.token) throw new Error('Не задан токен (настройки расширения)');
  const r = await fetch(c.botUrl.replace(/\/+$/, '') + path, {
    method: body ? 'POST' : 'GET',
    headers: { 'X-Crm-Token': c.token,
               ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error('Бот ответил HTTP ' + r.status);
  return r.json();
}

/* ⚠ ИСТОРИЯ ПОПЫТОК (просьба владельца 30.08: «если заявка не создалась — выдай
   причину и веди историю»). Уведомление живёт секунды, отчёт уходит боту — а
   владельцу нужно место, где видно КАЖДУЮ попытку с причиной. Пишем в
   storage.local кольцом на 100 записей; чистится кнопкой в попапе. */
async function history(item, res) {
  try {
    const l = (item && item.lead) || {};
    const запись = {
      t: Date.now(), dir: (item && item.dir) || '?',
      city: l.city || '', phone: l.phone || '',
      ok: !!(res && res.ok), id: (res && res.id) || null,
      skipped: !!(res && res.skipped), retry: !!(res && res.retry),
      why: res && res.ok ? '' :
        ((res && (res.errors || []).join('; ')) || (res && res.error) || 'без деталей'),
      warn: (res && res.warnings) || [],
    };
    const { istoriya = [] } = await chrome.storage.local.get('istoriya');
    istoriya.unshift(запись);
    await chrome.storage.local.set({ istoriya: istoriya.slice(0, 100) });
  } catch (_e) { /* история не должна ломать создание */ }
}

let pollBusy = false;  // 16.08: два параллельных poll() создали заявку ДВАЖДЫ

// единственность создания решает СЕРВЕР: захват ключа перед работой.
// Не захватил — кто-то уже создаёт (второй опрос, другой ПК), молча пропускаем
async function claimAndCreate(item) {
  try {
    const c = await api('/api/crm/claim', { key: item.key });
    if (!c.ok) return { ok: false, error: 'лид уже в работе — жду его отчёта' };
  } catch (e) {
    const r = { ok: false, error: 'захват не удался: ' + String(e.message || e) };
    history(item, r);
    return r;
  }
  return createViaOffscreen(item);
}

async function poll() {
  if (pollBusy) return;
  pollBusy = true;
  try { await pollInner(); } finally { pollBusy = false; }
}

async function pollInner() {
  let pending = [];
  try {
    const d = await api('/api/crm/queue');
    pending = d.pending || [];
    await chrome.storage.local.set({ lastPoll: Date.now(), lastError: '' });
  } catch (e) {
    await chrome.storage.local.set({ lastError: String(e.message || e) });
    chrome.action.setBadgeText({ text: '!' });
    chrome.action.setBadgeBackgroundColor({ color: '#f87171' });
    return;
  }
  const c = await cfg();
  pending = pending.filter(x => c.dirs[x.dir] !== false);
  await chrome.storage.local.set({ pending });
  chrome.action.setBadgeText({ text: pending.length ? String(pending.length) : '' });
  chrome.action.setBadgeBackgroundColor({ color: '#5b8cff' });
  if (c.mode === 'auto' && pending.length) {
    // 16.08: бот сам решает тип — Впервые/Повтор/Гарантия по диалогу
    for (const item of pending) await claimAndCreate(item);
    poll2();  // перечитать после создания
  }
}
async function poll2() { try { await poll(); } catch (_e) {} }

/* ⚠ СТАТУСЫ СОЗДАННЫХ ЗАЯВОК — РАЗ В ПОЛЧАСА (29.08). Заявка «Готов», «Отказ» или
   «Отмена Филиала» приносит 60 ₽, «Отмена КЦ» — СПИСЫВАЕТ 60 ₽. Без статусов бот мерил
   переписку, а не исход, и отличить выгодную заявку от убыточной было нечем.
   Опрашиваем редко и помалу: карточки читаются чужой сессией владельца, и нагружать
   CRM ради статистики нельзя. */
async function опроситьСтатусы() {
  try {
    const список = await api('/api/crm/tracked');
    const items = (список?.items || []).slice(0, 20);
    if (!items.length) return;
    await ensureOffscreen();
    const res = await chrome.runtime.sendMessage({ kind: 'crm-status', items });
    if (res?.статусы?.length) await api('/api/crm/status', { статусы: res.статусы });
  } catch (_e) { /* статистика не должна мешать работе расширения */ }
}

/* --- offscreen: DOMParser недоступен в service worker --- */
let creating = Promise.resolve();

async function ensureOffscreen() {
  const has = await chrome.offscreen.hasDocument?.();
  if (!has) {
    await chrome.offscreen.createDocument({
      url: 'offscreen.html',
      reasons: ['DOM_PARSER'],
      justification: 'Разбор HTML-форм лид-центров для создания заявок',
    });
  }
}

async function createViaOffscreen(item) {
  // строго по одному: CRM не любит параллельных сессионных постов
  creating = creating.then(() => createOne(item)).catch(() => {});
  return creating;
}

async function createOne(item) {
  // заявка уже создавалась, но отчёт боту не дошёл? Не создаём вторую —
  // только повторяем отчёт (иначе истёкший захват породил бы дубль в CRM)
  const мкл = 'created:' + item.key;
  const было = (await chrome.storage.local.get(мкл))[мкл];
  let res;
  if (было) {
    res = { ok: true, id: было.id || null, errors: [] };
  } else {
    await ensureOffscreen();
    res = await chrome.runtime.sendMessage({ kind: 'crm-create', item });
    if (res?.ok) await chrome.storage.local.set({ [мкл]: { id: res.id || null, t: Date.now() } });
  }
  // ВРЕМЕННАЯ ошибка (сессия CRM истекла, сеть, гонка захвата) — БЕЗ отчёта:
  // лид останется pending, захват истечёт, попробуем снова. failed — только
  // окончательное (ошибки валидации формы CRM).
  history(item, res);
  if (!res?.ok && res?.retry) {
    chrome.action.setBadgeText({ text: '⏳' });
    chrome.action.setBadgeBackgroundColor({ color: '#eab308' });
    chrome.notifications?.create({
      type: 'basic', iconUrl: 'icon48.png',
      title: 'Заявка отложена (' + item.dir + ')',
      message: ('повторю сам: ' + (res?.error || '')).slice(0, 140),
    });
    return res;
  }
  try {
    await api('/api/crm/result', {
      key: item.key, ok: !!res?.ok, id: res?.id || null,
      errors: res?.errors || (res?.error ? [String(res.error)] : []),
      // ⚠ ПРЕДУПРЕЖДЕНИЯ И СПРАВОЧНИК ТОЖЕ НАВЕРХ (26.08). Раньше они жили только в
      // попапе: владелец видел оранжевую строку, а бот — нет, и подобрать «Вид работ»
      // точнее было не по чему. Теперь бот копит настоящие списки опций по городам.
      warnings: res?.warnings || [],
      spravochnik: res?.справочник || null,
      // ⚠ ЧЕМ СОЗДАНА ЗАЯВКА. Разбирая пустой «Вид работ», нельзя было понять, какая
      // версия расширения стоит на офисном ПК, — а поведение между версиями разное.
      ext: chrome.runtime.getManifest().version,
      vid_rabot: res?.вид_работ || null,
    });
    if (res?.ok) await chrome.storage.local.remove(мкл); // отчёт дошёл — маркер не нужен
  } catch (_e) { /* отчёт не дошёл — маркер created остался, дубля не будет */ }
  if (!res?.ok) {
    chrome.notifications?.create({
      type: 'basic', iconUrl: 'icon48.png',
      title: 'Заявка не создалась (' + item.dir + ')',
      message: (res?.errors || [res?.error || 'без деталей']).join('; ').slice(0, 140),
    });
  }
  return res;
}

/* ─── СЛЕЖЕНИЕ ЗА БОТОМ (просьба владельца 30.08) ─────────────────────────────
   Расширение живёт в браузере, где владелец залогинен в LeadChat: access-токен
   берём через POST /auth/refresh по HttpOnly-куке сессии (credentials: include),
   ничего не храним. Диалог, который прямо сейчас ведёт бот, открывается ОДНОЙ
   вкладкой /chats/<id>: вкладка переиспользуется, а не плодится; бот перешёл в
   другой диалог — та же вкладка переезжает туда. */

async function leadchatToken() {
  const c = await cfg();
  const base = (c.leadchatUrl || '').replace(/\/+$/, '');
  if (!base) throw new Error('адрес LeadChat не задан (настройки)');
  const r = await fetch(base + '/api/v1/auth/refresh', {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' }, body: '{}',
  });
  if (!r.ok) throw new Error('LeadChat не пустил (HTTP ' + r.status +
    ') — войдите в LeadChat в этом браузере');
  const d = await r.json();
  const t = d.access_token || d.token || (d.tokens && d.tokens.access);
  if (!t) throw new Error('LeadChat не вернул токен');
  return { base, t };
}

async function botDialogs() {
  const { base, t } = await leadchatToken();
  // ⚠ У ручки списка НЕТ параметра bot_active — прежний запрос `?bot_active=true`
  // молча игнорировался, и «слежение за ботом» открывало просто последние диалоги,
  // включая те, что ведут люди (жалоба владельца 30.08). Серверный фильтр — только
  // «без ответственного»; «бот ведёт» отбираем по полям строки сами: bot_active
  // означает «бот включён», а не «бот ведёт» — принятый человеком диалог держит
  // флаг, но бот в нём молчит (заслон claimed_by_human).
  const r = await fetch(base + '/api/v1/conversations?unassigned=true&limit=50', {
    headers: { Authorization: 'Bearer ' + t },
  });
  if (!r.ok) throw new Error('диалоги не отдались (HTTP ' + r.status + ')');
  const d = await r.json();
  const raw = d.items || d.conversations || [];
  const items = raw.filter(
    (i) => i.bot_active && !i.assignee && i.status !== 'closed');
  return { base, items };
}

async function followBotNow(manual) {
  const c = await cfg();
  if (!manual && !c.followBot) return;
  let base, items;
  try { ({ base, items } = await botDialogs()); }
  catch (e) {
    if (manual) chrome.notifications?.create({
      type: 'basic', iconUrl: 'icon48.png', title: 'Слежение за ботом',
      message: String(e.message || e).slice(0, 140),
    });
    return;
  }
  if (!items.length) {
    if (manual) chrome.notifications?.create({
      type: 'basic', iconUrl: 'icon48.png', title: 'Слежение за ботом',
      message: 'Сейчас бот не ведёт ни одного диалога',
    });
    return;
  }
  const conv = items[0];
  const url = base + '/chats/' + conv.id;
  const st = await chrome.storage.local.get(['followTabId', 'followConvId']);
  // тот же диалог уже открыт — не дёргаем вкладку (F5 сбил бы владельцу чтение)
  if (!manual && st.followConvId === conv.id && st.followTabId) {
    try { await chrome.tabs.get(st.followTabId); return; } catch (_e) { /* вкладку закрыли */ }
  }
  let tab = null;
  if (st.followTabId) {
    try { tab = await chrome.tabs.update(st.followTabId, { url, active: !!manual }); }
    catch (_e) { tab = null; }
  }
  if (!tab) tab = await chrome.tabs.create({ url, active: !!manual });
  await chrome.storage.local.set({ followTabId: tab.id, followConvId: conv.id });
}

chrome.alarms.create('follow', { periodInMinutes: 1, delayInMinutes: 1 });

/* кнопки из popup */
chrome.runtime.onMessage.addListener((msg, _s, respond) => {
  if (msg?.kind === 'create-now') {
    claimAndCreate(msg.item).then(r => { poll2(); respond(r); });
    return true;
  }
  if (msg?.kind === 'poll-now') { poll().then(() => respond({ ok: true })); return true; }
  if (msg?.kind === 'follow-now') {
    followBotNow(true).then(() => respond({ ok: true }));
    return true;
  }
  if (msg?.kind === 'history-clear') {
    chrome.storage.local.set({ istoriya: [] }).then(() => respond({ ok: true }));
    return true;
  }
  /* Снять лид из очереди руками (просьба владельца 25.08): дубль, ошибка бота,
     клиент передумал. Заявку в CRM НЕ трогаем — ручка чистит только очередь бота. */
  if (msg?.kind === 'drop-now') {
    api('/api/crm/drop', { key: msg.key, reason: msg.reason || '' })
      .then(r => { poll2(); respond(r); })
      .catch(e => respond({ ok: false, error: String(e.message || e) }));
    return true;
  }
});
