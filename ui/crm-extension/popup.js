const $ = s => document.querySelector(s);
const esc = s => (s || '').replace(/[&<>"]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));

async function render() {
  const { pending = [], lastPoll = 0, lastError = '' } =
    await chrome.storage.local.get(['pending', 'lastPoll', 'lastError']);
  $('#error').innerHTML = lastError ? `<div class="err">${esc(lastError)}</div>` : '';
  const list = $('#list');
  list.innerHTML = '';
  if (!pending.length) {
    list.innerHTML = '<div class="empty">Очередь пуста — бот пришлёт лид, когда диалог дойдёт до телефона и окна.</div>';
  }
  for (const item of pending) {
    const d = document.createElement('div');
    d.className = 'item';
    const l = item.lead || {};
    d.innerHTML = `
      <div class="row"><span class="dir">${esc(item.dir)}</span>
        <span class="city">${esc(l.city || 'город?')}</span>
        <span class="phone">${esc(l.phone || '')}</span></div>
      <div class="prob">${esc((l.comment || '').split('\n')[0])}</div>
      <div class="meta">${esc(l.name || '')} · партнёр ${esc(l.partner || '—')}${l.review ? ' · отзыв' : ''} · ${esc(item.t || '')}</div>
      ${l.repeat_hint ? `<div class="meta" style="color:#b45309">клиент упоминает ${l.repeat_hint === 'гарантия' ? 'гарантию' : 'наш прошлый визит'} — тип сверится с историей в базе</div>` : ''}
      <div class="row" style="margin-top:6px">
        <button data-key="${esc(item.key)}" class="mk primary">Создать заявку</button>
        <button data-key="${esc(item.key)}" class="rm" title="Убрать лид из очереди — заявка НЕ создаётся">Убрать</button>
      </div>`;
    list.append(d);
  }
  for (const b of document.querySelectorAll('.mk')) {
    b.onclick = async () => {
      b.disabled = true; b.textContent = 'Создаю…';
      const item = pending.find(x => x.key === b.dataset.key);
      const r = await chrome.runtime.sendMessage({ kind: 'create-now', item });
      if (r?.ok) b.textContent = 'Создана №' + (r.id || '');
      // ⚠ ОТКАЗ ПО ДУБЛЮ — НЕ ОШИБКА, а решение, и лид остаётся в очереди: его либо
      // заводят руками, либо снимают «Убрать». Красным это писать нельзя — иначе
      // диспетчер прочтёт как сбой и полезет чинить бота.
      else if (r?.skipped) b.textContent = String(r.error || 'не создаю').slice(0, 70);
      else b.textContent = 'Ошибка: ' + ((r?.errors || [r?.error]).join('; ') || '?').slice(0, 60);
      // «Создана, но поле пустое» видно только здесь: в карточку никто не заглянет
      if (r?.warnings?.length) {
        const w = document.createElement('div');
        w.className = 'meta';
        w.style.color = '#b45309';
        w.textContent = '⚠ ' + r.warnings.join('; ');
        b.closest('.item')?.append(w);
      }
      setTimeout(render, r?.warnings?.length ? 6000 : 1200);
    };
  }
  /* ⚠ СНЯТИЕ НЕОБРАТИМО: очередь — журнал дописыванием, снятый ключ закрыт навсегда.
     Поэтому спрашиваем подтверждение, а не убираем по одному промаху мышью. */
  for (const b of document.querySelectorAll('.rm')) {
    b.onclick = async () => {
      const item = pending.find(x => x.key === b.dataset.key) || {};
      const l = item.lead || {};
      if (!confirm(`Убрать лид из очереди?\n\n${l.city || ''} ${l.phone || ''}\n${(l.comment || '').split('\n')[0]}\n\nЗаявка в CRM создана НЕ будет. Вернуть лид в очередь нельзя.`)) return;
      b.disabled = true; b.textContent = 'Убираю…';
      const r = await chrome.runtime.sendMessage({ kind: 'drop-now', key: b.dataset.key });
      b.textContent = r?.ok ? 'Убран' : 'Ошибка: ' + String(r?.error || '?').slice(0, 40);
      setTimeout(render, 900);
    };
  }
  // ⚠ ВЕРСИЯ НА ВИДУ. Расширение ставится с диска и обновляется вручную, значит
  // «поставил ли я новое?» — вопрос, который задают после каждой правки. Пока
  // версии не было видно нигде, ответ приходилось искать на chrome://extensions.
  $('#status').textContent =
    (lastPoll ? 'Опрос: ' + new Date(lastPoll).toLocaleTimeString() : 'ещё не опрашивал')
    + ' · версия ' + chrome.runtime.getManifest().version;
}

$('#refresh').onclick = async () => { await chrome.runtime.sendMessage({ kind: 'poll-now' }); render(); };
$('#opts').onclick = () => chrome.runtime.openOptionsPage();
$('#all').onclick = async () => {
  const { pending = [] } = await chrome.storage.local.get('pending');
  for (const item of pending) await chrome.runtime.sendMessage({ kind: 'create-now', item });
  render();
};
chrome.storage.onChanged.addListener(render);
render();

/* ── История попыток (просьба владельца 30.08) ────────────────────────────── */
async function renderHist() {
  const { istoriya = [] } = await chrome.storage.local.get('istoriya');
  const h = $('#hist');
  if (!istoriya.length) {
    h.innerHTML = '<div class="empty">Истории пока нет — она появится с первой попыткой создать заявку.</div>';
    return;
  }
  h.innerHTML = '<div style="padding:6px 12px;text-align:right">' +
    '<button id="hist-clear" class="rm">Очистить историю</button></div>';
  for (const z of istoriya) {
    const d = document.createElement('div');
    d.className = 'h-item';
    const когда = new Date(z.t).toLocaleString('ru-RU',
      { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    const статус = z.ok ? `<span class="h-ok">✔ создана${z.id ? ' №' + esc(String(z.id)) : ''}</span>`
      : z.skipped ? '<span class="h-skip">↷ пропущена</span>'
      : z.retry ? '<span class="h-skip">⏳ отложена, повторю</span>'
      : '<span class="h-bad">✖ не создана</span>';
    d.innerHTML = `
      <div>${статус} · <b>${esc(z.dir)}</b> ${esc(z.city)} ${esc(z.phone)}</div>
      ${z.why ? `<div class="h-why">${esc(z.why)}</div>` : ''}
      ${(z.warn || []).length ? `<div class="h-why" style="color:#eab308">⚠ ${esc(z.warn.join('; '))}</div>` : ''}
      <div class="h-meta">${когда}</div>`;
    h.append(d);
  }
  $('#hist-clear').onclick = async () => {
    if (!confirm('Очистить историю попыток?')) return;
    await chrome.runtime.sendMessage({ kind: 'history-clear' });
    renderHist();
  };
}

function показать(вкладка) {
  const оч = вкладка === 'queue';
  $('#list').style.display = оч ? '' : 'none';
  $('#hist').style.display = оч ? 'none' : '';
  $('#tab-queue').classList.toggle('active', оч);
  $('#tab-hist').classList.toggle('active', !оч);
  if (!оч) renderHist();
}
$('#tab-queue').onclick = () => показать('queue');
$('#tab-hist').onclick = () => показать('hist');

$('#follow').onclick = async () => {
  $('#follow').disabled = true;
  await chrome.runtime.sendMessage({ kind: 'follow-now' });
  $('#follow').disabled = false;
  window.close();  // вкладка открылась — попап больше не нужен
};
