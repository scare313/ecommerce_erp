"""Barcode Scanner UI Component.

Provides two scanning modes:
1. Hardware Scanner: Reads keyboard-emulated input from USB/Bluetooth barcode scanners.
2. Camera Scanner: Injects Html5-QRCode JS library to use phone/webcam for scanning.

The camera component writes the scanned result back to Streamlit via
st.query_params so the page can react to it without a full-page reload.
"""
import streamlit as st
import streamlit.components.v1 as components

# ──────────────────────────────────────────────────────────────────────────────
# HTML / JS for the Camera Scanner using the html5-qrcode library
# ──────────────────────────────────────────────────────────────────────────────
_CAMERA_SCANNER_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Barcode Scanner</title>
  <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }

    body {
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      background: transparent;
    }

    #scanner-wrapper {
      border-radius: 16px;
      overflow: hidden;
      background: #0f1117;
      border: 1px solid rgba(255,255,255,0.08);
    }

    #reader {
      width: 100%;
      border-radius: 12px;
      overflow: hidden;
    }

    /* Override html5-qrcode default styles */
    #reader video {
      border-radius: 12px !important;
    }

    #reader__scan_region img {
      display: none !important;
    }

    #scanner-controls {
      padding: 12px 16px;
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }

    button {
      flex: 1;
      padding: 10px 18px;
      border: none;
      border-radius: 10px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
      min-width: 120px;
    }

    #startBtn {
      background: linear-gradient(135deg, #4ade80, #16a34a);
      color: #0f2e1a;
    }
    #startBtn:hover { opacity: 0.9; transform: scale(1.02); }

    #stopBtn {
      background: linear-gradient(135deg, #f87171, #dc2626);
      color: white;
      display: none;
    }
    #stopBtn:hover { opacity: 0.9; transform: scale(1.02); }

    #result-box {
      margin: 0 16px 12px;
      padding: 12px 16px;
      border-radius: 10px;
      background: rgba(74, 222, 128, 0.1);
      border: 1px solid rgba(74, 222, 128, 0.3);
      display: none;
    }

    #result-label {
      font-size: 11px;
      color: #4ade80;
      text-transform: uppercase;
      letter-spacing: 1px;
      font-weight: 600;
    }

    #result-value {
      font-size: 18px;
      font-weight: 700;
      color: #ffffff;
      margin-top: 4px;
      font-family: 'Courier New', monospace;
      word-break: break-all;
    }

    #status-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #6b7280;
      display: inline-block;
      margin-right: 6px;
      transition: background 0.3s;
    }

    #status-dot.active {
      background: #4ade80;
      box-shadow: 0 0 6px #4ade80;
      animation: pulse 1.5s infinite;
    }

    @keyframes pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }

    #status-text {
      font-size: 13px;
      color: #9ca3af;
    }

    #confirm-btn {
      background: linear-gradient(135deg, #6366f1, #4f46e5);
      color: white;
      width: calc(100% - 32px);
      margin: 0 16px 16px;
      display: none;
      padding: 12px;
      font-size: 15px;
    }
    #confirm-btn:hover { opacity: 0.9; transform: scale(1.01); }
  </style>
</head>
<body>
  <div id="scanner-wrapper">
    <div id="reader"></div>

    <div id="scanner-controls">
      <span id="status-dot"></span>
      <span id="status-text">Camera off</span>
      <button id="startBtn" onclick="startScanner()">📷 Start Camera</button>
      <button id="stopBtn"  onclick="stopScanner()">⏹ Stop</button>
    </div>

    <div id="result-box">
      <div id="result-label">✅ Scanned Barcode</div>
      <div id="result-value">–</div>
    </div>

    <button id="confirm-btn" onclick="confirmScan()">
      ✔ Use This SKU
    </button>
  </div>

  <script>
    let html5QrCode = null;
    let lastScanned = "";

    function startScanner() {
      document.getElementById('startBtn').style.display = 'none';
      document.getElementById('stopBtn').style.display  = 'flex';
      document.getElementById('status-dot').classList.add('active');
      document.getElementById('status-text').textContent = 'Scanning…';

      html5QrCode = new Html5Qrcode("reader");

      Html5Qrcode.getCameras().then(cameras => {
        if (!cameras || cameras.length === 0) {
          setStatus('No camera found.', false);
          return;
        }
        // Prefer rear/environment camera on phones
        const cam = cameras.find(c =>
          c.label.toLowerCase().includes('back') ||
          c.label.toLowerCase().includes('rear') ||
          c.label.toLowerCase().includes('environment')
        ) || cameras[cameras.length - 1];

        const config = {
          fps: 10,
          qrbox: { width: 260, height: 180 },
          aspectRatio: 1.7,
          formatsToSupport: [
            Html5QrcodeSupportedFormats.CODE_128,
            Html5QrcodeSupportedFormats.CODE_39,
            Html5QrcodeSupportedFormats.EAN_13,
            Html5QrcodeSupportedFormats.EAN_8,
            Html5QrcodeSupportedFormats.UPC_A,
            Html5QrcodeSupportedFormats.QR_CODE,
          ]
        };

        html5QrCode.start(cam.id, config, onScanSuccess, onScanError)
          .catch(err => setStatus('Camera error: ' + err, false));
      }).catch(err => {
        setStatus('Camera access denied. Please allow camera permission.', false);
      });
    }

    function stopScanner() {
      if (html5QrCode) {
        html5QrCode.stop().then(() => {
          document.getElementById('startBtn').style.display = 'flex';
          document.getElementById('stopBtn').style.display  = 'none';
          document.getElementById('status-dot').classList.remove('active');
          document.getElementById('status-text').textContent = 'Camera off';
        });
      }
    }

    function onScanSuccess(decodedText) {
      if (decodedText === lastScanned) return;
      lastScanned = decodedText;

      // Play a subtle beep
      try {
        const ctx = new AudioContext();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.type = 'sine';
        osc.frequency.value = 880;
        gain.gain.setValueAtTime(0.3, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
        osc.start(ctx.currentTime);
        osc.stop(ctx.currentTime + 0.25);
      } catch(e) {}

      // Show result
      document.getElementById('result-box').style.display = 'block';
      document.getElementById('result-value').textContent = decodedText;
      document.getElementById('confirm-btn').style.display = 'block';
      setStatus('Barcode detected!', true);
    }

    function onScanError(error) {
      // Suppress noisy frame errors
    }

    function confirmScan() {
      // Post the scanned value to the Streamlit parent frame
      window.parent.postMessage({
        type: 'barcode_scan',
        value: lastScanned
      }, '*');
    }

    function setStatus(msg, active) {
      const dot = document.getElementById('status-dot');
      const txt = document.getElementById('status-text');
      txt.textContent = msg;
      if (active) dot.classList.add('active');
      else dot.classList.remove('active');
    }
  </script>
</body>
</html>
"""


def render_camera_scanner(height: int = 460) -> None:
    """Render the camera-based barcode scanner component.

    The scanned barcode is written to ``st.session_state.camera_scan_result``
    via a hidden text_input that listens for postMessage events from the iframe.

    Args:
        height: Height of the iframe in pixels (default 460).
    """
    # ── Listener JS that bridges iframe → Streamlit session_state ────────────
    listener_html = """
    <script>
    window.addEventListener('message', function(event) {
      if (event.data && event.data.type === 'barcode_scan') {
        const val = event.data.value;
        // Write value into the hidden Streamlit text input
        const inputs = window.parent.document.querySelectorAll('input[data-testid="stTextInput"]');
        for (const inp of inputs) {
          if (inp.id && inp.id.includes('barcode_receiver')) {
            inp.value = val;
            inp.dispatchEvent(new Event('input', { bubbles: true }));
            inp.dispatchEvent(new Event('change', { bubbles: true }));
            break;
          }
        }
        // Fallback: set on the first available hidden input with our marker attr
        const marker = window.parent.document.querySelector('[data-barcode-receiver]');
        if (marker) {
          marker.value = val;
          marker.dispatchEvent(new Event('input', { bubbles: true }));
          marker.dispatchEvent(new Event('change', { bubbles: true }));
        }
      }
    });
    </script>
    """
    components.html(_CAMERA_SCANNER_HTML, height=height, scrolling=False)
    components.html(listener_html, height=0)


def render_hardware_scanner_input(label: str = "🔍 Scan or type SKU", key: str = "hw_scan_input") -> str:
    """Render a text input optimised for hardware barcode scanners.

    Hardware scanners emulate a keyboard and send the barcode string followed
    by an Enter key press, so a standard st.text_input captures it perfectly.
    This function wraps the input with a clear button and helpful styling.

    Args:
        label: Label to display above the input.
        key:   Unique Streamlit widget key.

    Returns:
        str: The current value of the input (stripped and uppercased).
    """
    col1, col2 = st.columns([5, 1])
    with col1:
        val = st.text_input(
            label,
            key=key,
            placeholder="Point scanner at barcode, or type SKU manually…",
            help="Hardware scanners send the barcode automatically. Press Enter to confirm.",
        )
    with col2:
        st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
        if st.button("✖ Clear", key=f"{key}_clear", use_container_width=True):
            st.session_state[key] = ""
            st.rerun()
    return (val or "").strip().upper()
