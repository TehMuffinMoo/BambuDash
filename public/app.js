const byId = (id) => document.getElementById(id);
const dialog = byId('connect-dialog');
let tokenMode = false;
let lastSnapshot = null;
let selectedPrinter = null;
let lastAmsSignature = '';
let demoMode = false;
let serverUnavailable = false;
const themeKey = 'bambudash-theme';

const preview = {
  devices: [
    { name: 'Bambu Lab P2S', model: 'P2S SERIES', id: 'DEMO-P2S', online: true },
    { name: 'Bambu Lab X1 Carbon', model: 'X1 CARBON', id: 'DEMO-X1', online: true },
    { name: 'Bambu Lab A1', model: 'A1', id: 'DEMO-A1', online: true }
  ],
  reports: {
    'DEMO-P2S': { print: { gcode_state: 'RUNNING', subtask_name: 'desktop-organizer.3mf', mc_percent: 68, mc_remaining_time: 84, layer_num: 136, total_layer_num: 200, nozzle_temper: 220, nozzle_target_temper: 220, bed_temper: 55, bed_target_temper: 55, chamber_temper: 32, device: { aps: { pm: 8, gear: 15 } }, ams: { tray_now: '1', tray_exist_bits: 'f', ams: [{ id: '0', humidity_raw: '38', temp: '24', tray: [{id: '0', tray_type: 'PLA', tray_color: 'E8E7DDFF'}, {id: '1', tray_type: 'PLA', tray_sub_brands: 'PLA Basic', tray_color: '86A69AFF'}, {id: '2', tray_type: 'PETG', tray_color: 'DCA482FF'}, {id: '3', tray_type: 'PLA', tray_color: '515C70FF'}] }] } } },
    'DEMO-X1': { print: { gcode_state: 'PAUSE', subtask_name: 'planter.3mf', mc_percent: 42, mc_remaining_time: 55, layer_num: 84, total_layer_num: 200, nozzle_temper: 240, nozzle_target_temper: 240, bed_temper: 70, bed_target_temper: 70, chamber_temper: 38, ams: { tray_now: '0', tray_exist_bits: '1', ams: [{ id: '0', humidity_raw: '32', temp: '26', tray: [{id: '0', tray_type: 'PETG', tray_color: '76A9B6FF'}] }] } } },
    'DEMO-A1': { _updated_at: 1, print: { gcode_state: 'IDLE', nozzle_temper: 25, nozzle_target_temper: 0, bed_temper: 24, bed_target_temper: 0 } }
  }
};

function setText(id, value) { byId(id).textContent = value; }
function valueOrDash(value) { return value === undefined || value === null || value === '' ? '—' : value; }
function temperature(value) { return value === undefined || value === null ? '—' : `${Math.round(Number(value))}°C`; }
function duration(minutes) {
  if (minutes === undefined || minutes === null || !Number.isFinite(Number(minutes))) return '—';
  const total = Math.max(0, Math.round(Number(minutes)));
  return total >= 60 ? `${Math.floor(total / 60)}h ${total % 60}m` : `${total}m`;
}
function stateLabel(state) {
  const states = { RUNNING: 'Printing', PREPARE: 'Preparing', PAUSE: 'Paused', FINISH: 'Completed', SUCCESS: 'Completed', FAILED: 'Failed', IDLE: 'Idle', SLICING: 'Preparing' };
  return states[state] || (state ? String(state).toLowerCase().replace(/^./, c => c.toUpperCase()) : 'Waiting');
}
function currentFilament(print) {
  const extruder = print.device?.extruder;
  const activeExtruder = (Number(extruder?.state || 0) >> 4) & 15;
  const source = extruder?.info?.find(item => Number(item.id) === activeExtruder)?.snow;
  if (source !== undefined && source !== null && Number.isInteger(Number(source))) {
    const number = Number(source);
    const unit = number >> 8;
    return unit >= 254 ? null : { unit, slot: number & 255 };
  }
  const legacy = print.ams?.tray_now;
  if (legacy === undefined || legacy === null || legacy === '') return null;
  const number = Number(legacy);
  if (!Number.isInteger(number) || number < 0 || number >= 254) return null;
  return number >= 80 ? { unit: number, slot: 0 } : { unit: Math.floor(number / 4), slot: number % 4 };
}
function filamentColor(value) {
  const color = String(value || '').replace(/^#/, '');
  return /^[\da-f]{6}([\da-f]{2})?$/i.test(color) ? `#${color.slice(0, 6)}` : '#bdc6bd';
}
function fitPrintPreview() {
  const image = byId('job-preview');
  image.style.removeProperty('object-view-box');
  // Cross-origin artwork cannot be inspected safely without CORS permission.
  if (!CSS.supports('object-view-box', 'inset(0)') ||
      new URL(image.currentSrc).origin !== location.origin) return;
  const width = image.naturalWidth;
  const height = image.naturalHeight;
  if (!width || !height || width * height > 4_000_000) return;
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  context.drawImage(image, 0, 0);
  const pixels = context.getImageData(0, 0, width, height).data;
  let left = width, top = height, right = -1, bottom = -1;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (pixels[(y * width + x) * 4 + 3] !== 0) {
        left = Math.min(left, x);
        right = Math.max(right, x);
        top = Math.min(top, y);
        bottom = Math.max(bottom, y);
      }
    }
  }
  if (right < 0) return;
  const padding = Math.ceil(Math.max(right - left + 1, bottom - top + 1) * 0.05);
  left = Math.max(0, left - padding);
  top = Math.max(0, top - padding);
  right = Math.min(width, right + 1 + padding);
  bottom = Math.min(height, bottom + 1 + padding);
  image.style.objectViewBox = `inset(${top / height * 100}% ${(width - right) / width * 100}% ${(height - bottom) / height * 100}% ${left / width * 100}%)`;
}
function renderPreview(report, state, isPreview) {
  const image = byId('job-preview');
  const active = ['RUNNING', 'PREPARE', 'PAUSE'].includes(state);
  const url = active && !isPreview ? report._preview_url : null;
  if (url) {
    if (image.dataset.url !== url) {
      image.dataset.url = url;
      image.style.removeProperty('object-view-box');
      image.src = url;
      image.hidden = false;
      byId('preview-empty').hidden = true;
    }
  } else {
    delete image.dataset.url;
    image.hidden = true;
    byId('preview-empty').hidden = false;
    const statuses = { loading: 'Loading print preview', plate_missing: 'Active plate not reported', printer_address_missing: 'Printer address unavailable', access_code_missing: 'Printer access code unavailable', printer_media_unavailable: 'Printer media unavailable', cloud_preview_unavailable: 'Cloud preview unavailable' };
    setText('preview-message', isPreview ? 'Preview not available in demo' : active ? statuses[report._preview_status] || 'Print preview unavailable' : 'No active print');
  }
}
function renderAms(report, isPreview, connection) {
  const print = report.print || {};
  const ams = print.ams || {};
  const active = currentFilament(print);
  const signature = JSON.stringify([ams, active, print.gcode_state, report.info?.module, isPreview, connection]);
  if (signature === lastAmsSignature) return;
  lastAmsSignature = signature;
  const container = byId('ams-units');
  container.replaceChildren();
  const units = Array.isArray(ams.ams) ? ams.ams.filter(unit => unit && unit.id != null) : [];
  const exists = typeof ams.ams_exist_bits === 'string' && /^[\da-f]+$/i.test(ams.ams_exist_bits) ? parseInt(ams.ams_exist_bits, 16) : null;
  const visibleUnits = units.filter(unit => exists === null || Number(unit.id) >= 32 || (exists & (1 << Number(unit.id))) !== 0);
  setText('ams-summary', isPreview ? 'DEMO DATA' : connection === 'disconnected' ? 'NOT CONNECTED' : connection !== 'connected' ? 'LAST REPORTED DATA' : `${visibleUnits.length} ${visibleUnits.length === 1 ? 'UNIT' : 'UNITS'}`);
  if (!visibleUnits.length) {
    const empty = document.createElement('p');
    empty.className = 'ams-empty';
    empty.textContent = connection === 'disconnected' && !isPreview ? 'Connect a printer to view filament.' : report._updated_at ? 'No AMS units reported by this printer.' : 'Waiting for AMS data from the printer.';
    container.append(empty);
    return;
  }
  visibleUnits.sort((first, second) => Number(first.id) - Number(second.id));
  for (const unit of visibleUnits) {
    const unitId = Number(unit.id);
    const unitElement = document.createElement('article');
    unitElement.className = 'ams-unit';
    unitElement.innerHTML = '<div class="ams-unit-header"><div><span class="ams-unit-label">AUTOMATIC MATERIAL SYSTEM</span><h3></h3></div><div class="ams-unit-meta"></div></div><div class="ams-housing"><div class="ams-housing-top"><span>AMS</span><span class="ams-led"></span></div><div class="ams-slots"></div><div class="ams-housing-base"><span>FILAMENT SYSTEM</span><span>● ● ●</span></div></div>';
    const module = report.info?.module?.find(item => /^(ams|n3f)\//.test(item.name || '') && Number(item.name.split('/')[1]) === unitId);
    unitElement.querySelector('h3').textContent = module?.product_name || `AMS ${unitId + 1}`;
    const meta = [];
    if (unit.temp !== undefined && unit.temp !== '' && Number.isFinite(Number(unit.temp))) meta.push(`${Number(unit.temp)}°C`);
    if (unit.humidity_raw !== undefined && unit.humidity_raw !== '' && Number.isFinite(Number(unit.humidity_raw))) meta.push(`${Number(unit.humidity_raw)}% humidity`);
    unitElement.querySelector('.ams-unit-meta').textContent = meta.join('  ·  ');
    const slots = unitElement.querySelector('.ams-slots');
    const trays = Array.isArray(unit.tray) ? unit.tray : [];
    const trayBits = typeof ams.tray_exist_bits === 'string' && /^[\da-f]+$/i.test(ams.tray_exist_bits) ? BigInt(`0x${ams.tray_exist_bits}`) : null;
    const slotCount = unitId >= 128 ? 1 : Math.max(4, ...trays.map(tray => Number(tray.id) + 1).filter(Number.isFinite));
    for (let slot = 0; slot < slotCount; slot++) {
      const tray = trays.find(item => Number(item.id) === slot);
      const bitIndex = unitId * 4 + slot;
      const present = trayBits !== null && bitIndex < 64 ? Boolean(trayBits & (1n << BigInt(bitIndex))) : Boolean(tray && (tray.tray_type || tray.tray_color || (Number(tray.state) & 1)));
      const isActive = present && active?.unit === unitId && active?.slot === slot;
      const spinning = isActive && print.gcode_state === 'RUNNING' && (isPreview || connection === 'connected');
      const slotElement = document.createElement('div');
      slotElement.className = `ams-slot${isActive ? ' is-active' : ''}${spinning ? ' is-spinning' : ''}${present ? '' : ' is-empty'}`;
      slotElement.innerHTML = '<div class="slot-top"><span class="slot-number"></span><span class="slot-active"></span></div><div class="spool" aria-hidden="true"><div class="spool-winding"><span></span></div></div><div class="slot-name"></div><div class="slot-detail"></div>';
      slotElement.style.setProperty('--filament', filamentColor(tray?.tray_color));
      slotElement.querySelector('.slot-number').textContent = String(slot + 1).padStart(2, '0');
      slotElement.querySelector('.slot-active').textContent = isActive ? (spinning ? 'IN USE' : 'LOADED') : '';
      slotElement.querySelector('.slot-name').textContent = present ? (tray?.tray_sub_brands || tray?.tray_type || 'Unknown filament') : 'Empty';
      const remain = Number(tray?.remain);
      slotElement.querySelector('.slot-detail').textContent = present ? (Number.isFinite(remain) && remain >= 0 && remain <= 100 ? `${remain}% remaining` : tray?.tray_type || 'Filament') : 'No spool';
      slots.append(slotElement);
    }
    container.append(unitElement);
  }
}
function render(device, report, isPreview, connection) {
  const print = report.print || {};
  const state = print.gcode_state || device.print_status;
  const offline = !isPreview && device.online === false;
  if (byId('preview-badge')) {
    const badge = isPreview ? 'DEMO DATA' : offline ? 'OFFLINE' : connection === 'error' ? 'CONNECTION FAILED' : connection !== 'connected' ? 'RECONNECTING' : '';
    setText('preview-badge', badge);
    byId('preview-badge').hidden = !badge;
  }
  const percent = print.mc_percent;
  const validPercent = percent !== undefined && percent !== null && Number.isFinite(Number(percent));
  setText('printer-name', device.name || device.model || 'Your printer');
  setText('printer-model', (device.model || 'BAMBU LAB').toUpperCase());
  setText('printer-serial', isPreview ? 'PREVIEW' : device.id || '—');
  byId('printer-status').innerHTML = '<span></span>';
  byId('printer-status').append(document.createTextNode(offline ? 'Offline' : stateLabel(state)));
  setText('connection-detail', isPreview ? 'Cloud preview' : connection === 'connected' ? 'Bambu Cloud' : 'Reconnecting');
  setText('availability', isPreview ? 'Ready to connect' : device.online === false ? 'Offline' : device.online === true ? 'Online' : 'Unknown');
  setText('job-name', print.subtask_name || print.gcode_file || 'No active job');
  setText('job-state', offline ? 'OFFLINE' : state ? stateLabel(state).toUpperCase() : 'WAITING');
  renderPreview(report, offline ? null : state, isPreview);
  setText('job-title', offline ? 'Printer offline.' : state === 'RUNNING' ? 'A good idea, in progress.' : state === 'PAUSE' ? 'Print paused.' : state === 'FINISH' || state === 'SUCCESS' ? 'Print complete.' : 'Ready when you are.');
  setText('progress', validPercent ? Math.max(0, Math.min(100, Math.round(Number(percent)))) : '—');
  byId('progress-bar').style.width = validPercent ? `${Math.max(0, Math.min(100, Number(percent)))}%` : '0%';
  setText('remaining', duration(print.mc_remaining_time));
  const minutes = Number(print.mc_remaining_time);
  const showCompletion = !offline && ['RUNNING', 'PREPARE'].includes(state) && print.mc_remaining_time !== undefined && Number.isFinite(minutes);
  setText('completion', showCompletion ? new Date(Date.now() + minutes * 60000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '—');
  setText('layer', print.layer_num != null ? `${print.layer_num} / ${valueOrDash(print.total_layer_num)}` : '—');
  setText('nozzle', print.nozzle_temper == null ? '—' : Math.round(Number(print.nozzle_temper)));
  setText('nozzle-target', temperature(print.nozzle_target_temper));
  setText('bed', print.bed_temper == null ? '—' : Math.round(Number(print.bed_temper)));
  setText('bed-target', temperature(print.bed_target_temper));
  const packedChamber = print.device?.ctc?.info?.temp;
  const chamber = packedChamber != null && Number.isFinite(Number(packedChamber)) ? Number(packedChamber) & 0xffff : print.chamber_temper ?? print.info?.temp;
  setText('chamber', chamber == null || !Number.isFinite(Number(chamber)) ? '—' : Math.round(Number(chamber)));
  const aux = print.aux;
  const purifierPresent = typeof aux !== 'string' || !/^[\da-f]+$/i.test(aux) || (BigInt(`0x${aux}`) & (1n << 37n)) !== 0n;
  const particulate = print.device?.aps?.pm;
  setText('air-quality', purifierPresent && typeof particulate === 'number' && Number.isFinite(particulate) && particulate >= 0 ? particulate : '—');
  const gear = print.device?.aps?.gear;
  const fanModes = { 0: 'Off', 1: 'Low', 2: 'Medium', 3: 'High', 15: 'Auto' };
  setText('purifier-fan', purifierPresent && typeof gear === 'number' ? fanModes[gear] || '—' : '—');
  const cloudLabel = connection === 'connected' ? 'Cloud connected' : connection === 'error' ? 'Cloud connection failed' : 'Cloud reconnecting';
  setText('mode-label', isPreview ? 'DEMO DATA' : cloudLabel.toUpperCase());
  if (byId('top-connection')) setText('top-connection', isPreview ? 'Demo mode · not connected' : cloudLabel);
  setText('side-connection', isPreview ? 'Demo mode · not connected' : cloudLabel);
  setText('metrics-note', isPreview ? 'EXAMPLE READINGS' : 'PRINTER READINGS');
  setText('updated', isPreview ? 'EXAMPLE DATA · NOT CONNECTED' : report._updated_at ? `LAST UPDATE · ${new Date(report._updated_at * 1000).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit', second: '2-digit'})}` : 'WAITING FOR PRINTER DATA');
  setText('refresh-detail', isPreview ? 'Demo data · checked every 5 sec' : report._updated_at ? `Report ${new Date(report._updated_at * 1000).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit', second: '2-digit'})} · checked every 5 sec` : 'Waiting for report · checked every 5 sec');
  renderAms(report, isPreview, connection);
}

async function request(path, data) {
  const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Something went wrong. Please try again.');
  return result;
}
function showSnapshot() {
  const connection = lastSnapshot?.connection || 'disconnected';
  const hasSession = connection !== 'disconnected';
  if (hasSession) demoMode = false;
  const snapshot = demoMode ? preview : lastSnapshot;
  const printers = demoMode || hasSession ? snapshot.devices : [];
  const demoButton = byId('demo-button');
  demoButton.disabled = hasSession || (!demoMode && (serverUnavailable || !lastSnapshot));
  demoButton.textContent = demoMode ? 'Exit demo' : 'Show demo';
  const connectButton = byId('connect-button');
  connectButton.disabled = serverUnavailable || !lastSnapshot;
  connectButton.innerHTML = hasSession ? '<i data-lucide="log-out"></i><span>Disconnect</span>' : '<i data-lucide="plus"></i><span>Connect printer</span>';
  const selector = byId('printer-select');
  selector.replaceChildren(...printers.map(device => new Option(device.name || device.model || device.id, device.id)));
  if (!printers.some(device => device.id === selectedPrinter)) {
    selectedPrinter = (printers.find(device => (device.model || '').toUpperCase().includes('P2S')) || printers[0] || {}).id || null;
  }
  selector.value = selectedPrinter || '';
  selector.hidden = printers.length < 2;
  const device = printers.find(item => item.id === selectedPrinter);
  const showData = demoMode || Boolean(device);
  byId('printer-data').hidden = !showData;
  byId('connect-prompt').hidden = showData;
  if (device) {
    render(device, snapshot.reports[device.id] || {}, demoMode, connection);
  } else {
    const cloudLabel = connection === 'connected' ? 'Cloud connected' : connection === 'connecting' ? 'Cloud reconnecting' : connection === 'error' ? 'Cloud connection failed' : 'Not connected';
    if (byId('top-connection')) setText('top-connection', cloudLabel);
    setText('side-connection', cloudLabel);
    setText('printer-serial', '—');
    setText('updated', hasSession ? 'WAITING FOR PRINTER DATA' : 'NOT CONNECTED');
    renderAms({}, false, connection);
    const title = serverUnavailable ? 'Local server unavailable' : connection === 'connected' ? 'No printers found' : connection === 'connecting' ? 'Connecting to Bambu Cloud' : connection === 'error' ? 'Cloud connection failed' : 'Connect your printer';
    const message = serverUnavailable ? 'Check that the local dashboard server is running, then retry.' : connection === 'connected' ? 'No printers were found in this account. Add a printer to your account or disconnect to use another account.' : connection === 'connecting' ? 'Waiting for the cloud connection. You can disconnect and try another account.' : connection === 'error' ? 'Disconnect and try connecting again.' : 'Connect to Bambu Cloud to see your printer and live status.';
    setText('connect-prompt-title', title);
    setText('connect-prompt-message', message);
    setText('connect-prompt-button', serverUnavailable ? 'Retry' : hasSession ? 'Disconnect' : 'Connect printer');
    byId('connect-prompt-button').hidden = false;
  }
  if (serverUnavailable) {
    if (byId('top-connection')) setText('top-connection', 'Server unavailable');
    setText('side-connection', demoMode ? 'Demo mode · server unavailable' : 'Server unavailable');
    setText('updated', 'LOCAL SERVER UNAVAILABLE');
    if (demoMode) setText('refresh-detail', 'Demo data · local server unavailable');
    if (device && !demoMode) {
      setText('mode-label', 'SERVER UNAVAILABLE · LAST REPORTED DATA');
      setText('refresh-detail', 'Local server unavailable · last reported data');
      if (byId('preview-badge')) {
        setText('preview-badge', 'SERVER UNAVAILABLE');
        byId('preview-badge').hidden = false;
      }
    }
  }
  if (window.lucide) lucide.createIcons();
}

async function refresh() {
  try {
    const response = await fetch('/api/status', { cache: 'no-store' });
    if (!response.ok) throw new Error('Status unavailable');
    lastSnapshot = await response.json();
    serverUnavailable = false;
  } catch (error) {
    console.warn('Could not refresh printer status:', error);
    serverUnavailable = true;
  }
  showSnapshot();
}

async function connectAction() {
  if (lastSnapshot && lastSnapshot.connection !== 'disconnected') {
    try {
      await request('/api/disconnect', {});
      setText('settings-message', '');
      if (byId('connection-message')) setText('connection-message', '');
      await refresh();
    } catch (error) {
      setText('settings-message', `Could not disconnect: ${error.message}`);
      if (byId('connection-message')) setText('connection-message', `Could not disconnect: ${error.message}`);
      setText('connect-prompt-message', `Could not disconnect: ${error.message}`);
    }
  } else {
    setText('form-message', '');
    dialog.showModal();
  }
}
byId('connect-button').addEventListener('click', connectAction);
byId('connect-prompt-button').addEventListener('click', () => serverUnavailable ? refresh() : connectAction());
byId('demo-button').addEventListener('click', () => {
  demoMode = !demoMode;
  showSnapshot();
});
if (byId('settings-button')) {
  byId('settings-button').addEventListener('click', () => byId('settings-dialog').showModal());
  byId('close-settings').addEventListener('click', () => byId('settings-dialog').close());
  byId('settings-dialog').addEventListener('click', event => { if (event.target === byId('settings-dialog')) byId('settings-dialog').close(); });
}
function applyTheme(theme) {
  const value = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.dataset.theme = value;
  byId('theme-select').value = value;
}
try {
  applyTheme(localStorage.getItem(themeKey));
} catch (error) {
  console.warn('Could not load the saved theme:', error);
  setText('settings-message', 'Theme preference could not be loaded on this device.');
  applyTheme('light');
}
byId('theme-select').addEventListener('change', event => {
  applyTheme(event.target.value);
  try {
    localStorage.setItem(themeKey, event.target.value);
    setText('settings-message', '');
  } catch (error) {
    console.warn('Could not save the theme:', error);
    setText('settings-message', 'Theme preference could not be saved on this device.');
  }
});
window.addEventListener('storage', event => {
  if (event.key === themeKey) applyTheme(event.newValue);
});
let updateVersion = null;
let updateInProgress = false;
let installedVersion = null;
function showUpdateStatus(status) {
  installedVersion = status.current;
  updateVersion = status.available ? status.latest : null;
  byId('install-update').hidden = !updateVersion;
  const detail = status.state === 'error' ? status.message : status.available ? `Update v${status.latest} available` : status.latest ? 'Up to date' : 'No published releases';
  setText('update-status', `v${status.current} · ${detail}`);
}
async function readUpdateStatus() {
  const response = await fetch('/api/update/status', { cache: 'no-store' });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Update status unavailable.');
  return result;
}
async function checkUpdates() {
  if (updateInProgress) return;
  byId('check-updates').disabled = true;
  setText('update-status', `${installedVersion ? `v${installedVersion} · ` : ''}Checking for updates...`);
  try {
    const status = await request('/api/update/check', {});
    if (status.state === 'installing' || status.state === 'restarting') {
      watchUpdate(status.target);
    } else {
      showUpdateStatus(status);
    }
  } catch (error) {
    byId('install-update').hidden = true;
    setText('update-status', `${installedVersion ? `v${installedVersion} · ` : ''}Update check failed: ${error.message}`);
  } finally {
    if (!updateInProgress) byId('check-updates').disabled = false;
  }
}
byId('check-updates').addEventListener('click', checkUpdates);
function watchUpdate(target) {
  updateInProgress = true;
  byId('check-updates').disabled = true;
  byId('install-update').disabled = true;
  const started = Date.now();
  async function waitForUpdate() {
    if (Date.now() - started > 15 * 60 * 1000) {
      updateInProgress = false;
      byId('check-updates').disabled = false;
      byId('install-update').disabled = false;
      setText('update-status', 'Update status unavailable. Check the server logs before retrying.');
      return;
    }
    try {
      const status = await readUpdateStatus();
      if (status.state === 'error') {
        updateInProgress = false;
        byId('check-updates').disabled = false;
        byId('install-update').disabled = false;
        showUpdateStatus(status);
        return;
      }
      if (status.state === 'idle' && status.current === target) {
        updateInProgress = false;
        byId('check-updates').disabled = false;
        byId('install-update').disabled = false;
        showUpdateStatus(status);
        setText('update-status', `Updated to v${target}.`);
        return;
      }
      setText('update-status', status.message || `Installing v${target}...`);
    } catch (error) {
      setText('update-status', 'Waiting for BambuDash...');
    }
    window.setTimeout(waitForUpdate, 2000);
  }
  window.setTimeout(waitForUpdate, 2000);
}
byId('install-update').addEventListener('click', async () => {
  if (!updateVersion || updateInProgress) return;
  const target = updateVersion;
  if (!window.confirm(`Install BambuDash v${target} and restart? You may need to reconnect to Bambu Cloud if your session is not remembered.`)) return;
  updateInProgress = true;
  byId('check-updates').disabled = true;
  byId('install-update').disabled = true;
  setText('update-status', `Installing v${target}...`);
  try {
    await request('/api/update/install', { version: target });
  } catch (error) {
    updateInProgress = false;
    byId('check-updates').disabled = false;
    byId('install-update').disabled = false;
    setText('update-status', `Update failed: ${error.message}`);
    return;
  }
  watchUpdate(target);
});
async function initializeUpdates() {
  try {
    const status = await readUpdateStatus();
    if (status.state === 'installing' || status.state === 'restarting') {
      watchUpdate(status.target);
      return;
    }
    showUpdateStatus(status);
  } catch (error) {
    setText('update-status', `Version unavailable: ${error.message}`);
  }
  await checkUpdates();
}
initializeUpdates();
window.setInterval(checkUpdates, 6 * 60 * 60 * 1000);
byId('close-dialog').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });
byId('toggle-auth').addEventListener('click', () => {
  tokenMode = !tokenMode;
  byId('email-fields').hidden = tokenMode;
  byId('token-fields').hidden = !tokenMode;
  setText('toggle-auth', tokenMode ? 'Use email verification instead' : 'Use an access token instead');
  setText('form-message', '');
});
byId('send-code').addEventListener('click', async () => {
  const button = byId('send-code');
  button.disabled = true;
  setText('form-message', 'Sending verification code…');
  try {
    await request('/api/send-code', { email: byId('email').value, region: byId('region').value });
    setText('form-message', 'Code sent. Check your email.');
  } catch (error) { setText('form-message', error.message); }
  finally { button.disabled = false; }
});
byId('connect-form').addEventListener('submit', async event => {
  event.preventDefault();
  const button = byId('submit-connect');
  button.disabled = true;
  setText('form-message', 'Connecting to Bambu Cloud…');
  try {
    await request('/api/connect', { email: byId('email').value, code: byId('code').value, token: tokenMode ? byId('token').value : '', region: byId('region').value, remember: Boolean(byId('remember-session')?.checked) });
    byId('code').value = '';
    byId('token').value = '';
    dialog.close();
    await refresh();
  } catch (error) { setText('form-message', error.message); }
  finally { button.disabled = false; }
});
byId('refresh-button').addEventListener('click', refresh);
byId('job-preview').addEventListener('load', fitPrintPreview);
if (byId('job-preview').complete && byId('job-preview').naturalWidth) fitPrintPreview();
byId('job-preview').addEventListener('error', () => {
  byId('job-preview').hidden = true;
  byId('preview-empty').hidden = false;
  setText('preview-message', 'Print preview unavailable');
});
byId('printer-select').addEventListener('change', event => { selectedPrinter = event.target.value; refresh(); });
const now = new Date();
setText('today-day', now.toLocaleDateString([], { weekday: 'long' }));
setText('today-date', now.toLocaleDateString([], { month: 'long', day: 'numeric', year: 'numeric' }));
renderAms({}, false, 'disconnected');
showSnapshot();
refresh();
setInterval(refresh, 5000);