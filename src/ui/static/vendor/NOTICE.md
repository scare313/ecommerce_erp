# Vendored third-party assets

## html5-qrcode.min.js

- **Version:** 2.3.8 (pinned)
- **Source:** https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js
- **Upstream project:** https://github.com/mebjas/html5-qrcode
- **License:** Apache-2.0
- **SHA-256:** `660b12437b1d747e3e68b8be0685c08cb728140110ad213f167b14b66f8b1d8e`
- **Why vendored:** previously loaded from the unpkg CDN at runtime inside the
  camera scanner iframe (`src/ui/components/barcode_scanner.py`). Vendoring
  removes the runtime dependency on an external CDN for the app's core
  floor-scanning action — better reliability on poor networks, no third-party
  request on every scan.
- **How it's used:** read from disk at import time and inlined directly into
  the scanner's srcdoc HTML (`<script>{content}</script>`), consistent with
  how the component already embeds its own CSS/JS as string constants.

To update the pinned version: download the new `html5-qrcode@<version>.min.js`
build, replace this file, verify the new SHA-256, and update this notice.
