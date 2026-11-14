let amsIndex = 0;
let keyboardTarget = null;
let keyboardShift = false;

const rememberLabel = document.createElement('label');
rememberLabel.className = 'remember-option';
rememberLabel.innerHTML = '<input id="remember-session" type="checkbox"><span>Remember this session on this device<br><small>Stores your cloud token unencrypted, accessible to this OS user.</small></span>';
document.querySelector('.privacy-note').replaceWith(rememberLabel);

function showPage(name) {
  document.querySelectorAll('.page').forEach(page => {
    const active = page.id === `page-${name}`;
    page.hidden = !active;
    page.classList.toggle('active', active);
  });
  document.querySelectorAll('.nav-button').forEach(button => {
    const active = button.dataset.page === name;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  if (name === 'filament') showAms();
}
function showAms() {
  const units = [...document.querySelectorAll('#ams-units .ams-unit')];
  amsIndex = Math.max(0, Math.min(amsIndex, units.length - 1));
  units.forEach((unit, index) => unit.classList.toggle('active-unit', index === amsIndex));
  document.getElementById('ams-position').textContent = units.length ? `${amsIndex + 1} / ${units.length}` : '0 / 0';
  document.getElementById('ams-prev').disabled = amsIndex <= 0;
  document.getElementById('ams-next').disabled = amsIndex >= units.length - 1;
}
document.querySelectorAll('.nav-button').forEach(button => button.addEventListener('click', () => showPage(button.dataset.page)));
document.getElementById('ams-prev').addEventListener('click', () => { amsIndex--; showAms(); });
document.getElementById('ams-next').addEventListener('click', () => { amsIndex++; showAms(); });
new MutationObserver(showAms).observe(document.getElementById('ams-units'), { childList: true });
showAms();

function renderKeyboard() {
  const keyboard = document.getElementById('touch-keyboard');
  keyboard.replaceChildren();
  const rows = keyboardTarget?.id === 'code' ? [['1','2','3'],['4','5','6'],['7','8','9'],['⌫','0','Done']] : [['1','2','3','4','5','6','7','8','9','0'],['q','w','e','r','t','y','u','i','o','p'],['a','s','d','f','g','h','j','k','l'],['Shift','z','x','c','v','b','n','m','⌫'],['@','.','-','_','+','Space','/','Done']];
  for (const keys of rows) {
    const row = document.createElement('div');
    row.className = 'keyboard-row';
    for (const key of keys) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = keyboardShift && key.length === 1 ? key.toUpperCase() : key;
      if (key === 'Space') button.className = 'space';
      if (key === 'Shift' || key === 'Done') button.className = 'wide';
      button.addEventListener('pointerdown', event => event.preventDefault());
      button.addEventListener('click', () => {
        if (key === 'Done') { keyboard.hidden = true; keyboardTarget.blur(); return; }
        if (key === 'Shift') { keyboardShift = !keyboardShift; renderKeyboard(); return; }
        const start = keyboardTarget.selectionStart ?? keyboardTarget.value.length;
        const end = keyboardTarget.selectionEnd ?? start;
        const value = key === '⌫' ? '' : key === 'Space' ? ' ' : keyboardShift ? key.toUpperCase() : key;
        keyboardTarget.setRangeText(value, key === '⌫' && start === end ? Math.max(0, start - 1) : start, end, 'end');
        keyboardTarget.dispatchEvent(new Event('input', { bubbles: true }));
        keyboardTarget.focus();
      });
      row.append(button);
    }
    keyboard.append(row);
  }
}
document.querySelectorAll('#connect-dialog input[type="text"], #connect-dialog input[type="password"]').forEach(input => input.addEventListener('focus', () => {
  keyboardTarget = input;
  renderKeyboard();
  document.getElementById('touch-keyboard').hidden = false;
}));
document.getElementById('close-dialog').addEventListener('click', () => { document.getElementById('touch-keyboard').hidden = true; });
document.getElementById('connect-dialog').addEventListener('close', () => { document.getElementById('touch-keyboard').hidden = true; });