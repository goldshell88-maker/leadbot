const $ = s => document.querySelector(s);
const DEFAULTS = { botUrl: 'https://72-56-68-159.sslip.io', token: '', mode: 'manual',
                   intervalSec: 60, dirs: { kp: true, bt: true, mnc: true },
                   leadchatUrl: 'https://188-225-34-82.sslip.io', followBot: false };

async function load() {
  const c = { ...DEFAULTS, ...(await chrome.storage.sync.get(DEFAULTS)) };
  $('#botUrl').value = c.botUrl;
  $('#token').value = c.token;
  $('#intervalSec').value = c.intervalSec;
  document.querySelector(`input[name=mode][value=${c.mode}]`).checked = true;
  for (const d of ['kp', 'bt', 'mnc']) $('#d-' + d).checked = c.dirs[d] !== false;
  $('#leadchatUrl').value = c.leadchatUrl || '';
  $('#followBot').checked = !!c.followBot;
}

$('#save').onclick = async () => {
  await chrome.storage.sync.set({
    botUrl: $('#botUrl').value.trim().replace(/\/+$/, ''),
    token: $('#token').value.trim(),
    mode: document.querySelector('input[name=mode]:checked').value,
    intervalSec: Math.max(30, Number($('#intervalSec').value) || 60),
    dirs: { kp: $('#d-kp').checked, bt: $('#d-bt').checked, mnc: $('#d-mnc').checked },
    leadchatUrl: $('#leadchatUrl').value.trim().replace(/\/+$/, ''),
    followBot: $('#followBot').checked,
  });
  $('#msg').textContent = 'Сохранено';
  $('#msg').className = 'ok';
};

$('#test').onclick = async () => {
  $('#msg').textContent = 'Проверяю…'; $('#msg').className = '';
  try {
    const r = await fetch($('#botUrl').value.trim().replace(/\/+$/, '') + '/api/crm/queue?peek=1',  // подглядеть, НЕ арендуя лиды
      { headers: { 'X-Crm-Token': $('#token').value.trim() } });
    if (r.status === 401) throw new Error('бот отвечает, но токен не подошёл');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    $('#msg').textContent = 'Связь есть, лидов в очереди: ' + (d.pending || []).length;
    $('#msg').className = 'ok';
  } catch (e) {
    $('#msg').textContent = 'Ошибка: ' + (e.message || e);
    $('#msg').className = 'bad';
  }
};
load();
