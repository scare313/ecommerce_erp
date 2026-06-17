"""Barcode Scanner UI Component.

Provides two scanning modes:
1. Hardware Scanner: Reads keyboard-emulated input from USB/Bluetooth barcode scanners.
2. Camera Scanner: Uses the Html5-QRCode JS library rendered via
   streamlit.components.v1.html (an HTML srcdoc iframe).

Camera UX flow (zero extra steps):
  - Camera auto-starts on load
  - On successful scan: beeps, stops camera, auto-fills the parent Streamlit
    text input, dispatches Enter key -> Streamlit reruns automatically
  - After "Add to Queue": scan_counter increments -> iframe recreated -> camera
    auto-restarts

Secure-context requirement:
  Browser camera access (getUserMedia) only works on a secure context —
  https:// or http://localhost. Opening the app over a plain-HTTP LAN address
  (e.g. http://192.168.x.x:8501) on a phone will silently block the camera.
  Use HTTPS or a tunnel for phone scanning. Hardware-scanner mode has no such
  requirement and works everywhere.

Input clearing is handled by the caller via a counter-based widget key —
do NOT set st.session_state[key] inside this component.
"""
import streamlit as st
from streamlit.components.v1 import html as components_html

# ──────────────────────────────────────────────────────────────────────────────
# Camera Scanner HTML  (auto-start + auto-confirm, no manual steps)
# ──────────────────────────────────────────────────────────────────────────────

_CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Inter',sans-serif; background:transparent; }
#wrap { border-radius:16px; overflow:hidden; background:#0f1117; border:1px solid rgba(255,255,255,0.08); }
#reader { width:100%; max-height:300px; overflow:hidden; display:flex; justify-content:center; }
#reader video { border-radius:12px !important; width:100% !important; height:300px !important; object-fit:cover !important; }
#reader__scan_region img { display:none !important; }
#controls { padding:10px 14px; display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.btn { flex:1; padding:9px 16px; border:none; border-radius:10px; font-size:13px; font-weight:600; cursor:pointer; transition:all .2s; min-width:110px; }
#stopBtn  { background:linear-gradient(135deg,#f87171,#dc2626); color:#fff; }
#startBtn { background:linear-gradient(135deg,#4ade80,#16a34a); color:#0f2e1a; display:none; }
#dot { width:9px; height:9px; border-radius:50%; background:#6b7280; display:inline-block; margin-right:6px; transition:background .3s; }
#dot.on  { background:#4ade80; box-shadow:0 0 6px #4ade80; animation:pulse 1.5s infinite; }
#dot.ok  { background:#4ade80; box-shadow:0 0 6px #4ade80; }
@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
#lbl { font-size:13px; color:#9ca3af; flex:1; }
#result { margin:0 14px 10px; padding:11px 14px; border-radius:10px; background:rgba(74,222,128,.1); border:1px solid rgba(74,222,128,.3); display:none; }
#r-tag  { font-size:10px; color:#4ade80; text-transform:uppercase; letter-spacing:1px; font-weight:700; }
#r-val  { font-size:17px; font-weight:700; color:#fff; margin-top:3px; font-family:'Courier New',monospace; word-break:break-all; }
#r-hint { font-size:11px; color:#6b7280; margin-top:5px; }
#err { margin:0 14px 10px; padding:11px 14px; border-radius:10px; background:rgba(248,113,113,.1); border:1px solid rgba(248,113,113,.3); color:#fca5a5; font-size:12px; display:none; }
"""

_JS = """
let qr = null, scanning = false, last = '';

window.addEventListener('load', () => setTimeout(start, 500));

function secureOk() {
  // getUserMedia requires https or localhost. Surface a clear message otherwise.
  if (window.isSecureContext) return true;
  const h = location.hostname;
  return h === 'localhost' || h === '127.0.0.1';
}

function start() {
  if (scanning) return;
  if (!secureOk()) {
    showErr('Camera needs HTTPS or localhost. Open the app over https:// '
          + 'to scan with this device camera, or use a hardware scanner.');
    setStatus('Camera unavailable', '');
    return;
  }
  document.getElementById('startBtn').style.display = 'none';
  document.getElementById('stopBtn').style.display  = 'flex';
  document.getElementById('result').style.display   = 'none';
  document.getElementById('err').style.display      = 'none';
  setStatus('Scanning…', 'on');
  last = '';
  qr = new Html5Qrcode('reader');
  Html5Qrcode.getCameras().then(cams => {
    if (!cams || !cams.length) { setStatus('No camera found.', ''); return; }
    const cam = cams.find(c =>
      /back|rear|environment/i.test(c.label)
    ) || cams[cams.length - 1];
    qr.start(cam.id, {
      fps: 10,
      qrbox: { width:240, height:160 },
      aspectRatio: 1.5,
      formatsToSupport: [
        Html5QrcodeSupportedFormats.CODE_128,
        Html5QrcodeSupportedFormats.CODE_39,
        Html5QrcodeSupportedFormats.EAN_13,
        Html5QrcodeSupportedFormats.EAN_8,
        Html5QrcodeSupportedFormats.UPC_A,
        Html5QrcodeSupportedFormats.QR_CODE,
      ]
    }, onScan, () => {}).then(() => scanning = true)
      .catch(e => { setStatus('Camera error', ''); showErr('Camera error: ' + e); });
  }).catch(() => {
    setStatus('Camera access denied', '');
    showErr('Camera access denied — tap "Allow" in the browser prompt, '
          + 'then tap Restart.');
  });
}

function stop() {
  if (!qr || !scanning) return;
  qr.stop().then(() => {
    scanning = false;
    document.getElementById('stopBtn').style.display  = 'none';
    document.getElementById('startBtn').style.display = 'flex';
    setStatus('Camera off', '');
  }).catch(() => {});
}

function onScan(text) {
  if (text === last || !scanning) return;
  last = text;
  beep();
  document.getElementById('result').style.display = 'block';
  document.getElementById('r-val').textContent  = text;
  document.getElementById('r-hint').textContent = 'Filling details…';
  setStatus('Got it!', 'ok');
  stop();
  setTimeout(() => pushToStreamlit(text), 400);
}

function pushToStreamlit(value) {
  try {
    const doc = window.parent.document;
    // Find the bridge input — placeholder contains "waiting"
    const inputs = [...doc.querySelectorAll('input[type="text"]')];
    const inp = inputs.find(i => i.placeholder && /waiting/i.test(i.placeholder))
             || inputs[inputs.length - 1];
    if (!inp) { hint('Input not found — type SKU manually.'); return; }
    // Use React native setter
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    setter.set.call(inp, value);
    inp.dispatchEvent(new Event('input',  { bubbles:true }));
    inp.dispatchEvent(new Event('change', { bubbles:true }));
    // Press Enter to trigger Streamlit rerun
    inp.focus();
    ['keydown','keypress','keyup'].forEach(t =>
      inp.dispatchEvent(new KeyboardEvent(t, { key:'Enter', keyCode:13, bubbles:true }))
    );
    setTimeout(() => inp.blur(), 120);
    hint('Product found — fill details below ↓');
  } catch(e) { hint('Auto-fill blocked — type the SKU manually.'); }
}

function beep() {
  try {
    const c = new AudioContext(), o = c.createOscillator(), g = c.createGain();
    o.connect(g); g.connect(c.destination);
    o.type = 'sine'; o.frequency.value = 920;
    g.gain.setValueAtTime(.3, c.currentTime);
    g.gain.exponentialRampToValueAtTime(.001, c.currentTime + .2);
    o.start(c.currentTime); o.stop(c.currentTime + .2);
  } catch(e) {}
}

function setStatus(msg, dotClass) {
  document.getElementById('lbl').textContent = msg;
  const d = document.getElementById('dot');
  d.className = dotClass;
}

function showErr(msg) {
  const e = document.getElementById('err');
  e.textContent = msg;
  e.style.display = 'block';
}

function hint(msg) { document.getElementById('r-hint').textContent = msg; }
"""

_CAMERA_SCANNER_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
  <style>{_CSS}</style>
</head>
<body>
  <div id="wrap">
    <div id="reader"></div>
    <div id="controls">
      <span id="dot"></span>
      <span id="lbl">Starting camera&hellip;</span>
      <button class="btn" id="stopBtn"  onclick="stop()">&#9646; Stop</button>
      <button class="btn" id="startBtn" onclick="start()">&#128247; Restart</button>
    </div>
    <div id="result">
      <div id="r-tag">&#10003; Scanned</div>
      <div id="r-val">&ndash;</div>
      <div id="r-hint">Loading&hellip;</div>
    </div>
    <div id="err"></div>
  </div>
  <script>{_JS}</script>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def render_camera_scanner(height: int = 420) -> None:
    """Render the auto-starting camera barcode scanner.

    Uses streamlit.components.v1.html (NOT st.iframe — that expects a URL src,
    not an HTML string). The HTML is rendered in a same-origin srcdoc iframe so
    the auto-fill bridge can reach the parent Streamlit input, and so the camera
    inherits the page's camera permission on a secure context.

    - Camera starts automatically on load.
    - On a successful scan: beeps, stops the camera, auto-fills the nearest
      Streamlit text input in the parent page, and dispatches Enter to trigger
      an immediate Streamlit rerun.
    - After Add to Queue (scan_counter increments), the iframe is recreated and
      the camera auto-restarts for the next item.

    Args:
        height: iframe height in pixels.
    """
    components_html(_CAMERA_SCANNER_HTML, height=height, scrolling=False)


def render_hardware_scanner_input(label: str = "Scan or type SKU", key: str = "hw_scan_input") -> str:
    """Render a text input for USB/Bluetooth hardware barcode scanners.

    Hardware scanners emulate a keyboard — they type the barcode + Enter.
    Clearing is handled by the caller via a counter-based key; this function
    never modifies session_state directly.

    Args:
        label: Input label.
        key:   Unique Streamlit widget key.

    Returns:
        str: Current input value, stripped and uppercased.
    """
    val = st.text_input(
        label,
        key=key,
        placeholder="Point scanner at barcode, or type SKU and press Enter…",
        help="Hardware scanners send barcode + Enter automatically.",
        autocomplete="off",
    )
    return (val or "").strip().upper()
