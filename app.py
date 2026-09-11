import os
import subprocess
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# Where MP3s land on your machine
OUTPUT_DIR = Path.home() / "Music" / "yt-mp3"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

PAGE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>YouTube → MP3</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 560px;
           margin: 60px auto; padding: 0 16px; }
    input, button { font-size: 16px; padding: 10px; width: 100%;
                    box-sizing: border-box; margin-top: 8px; }
    button { background: #2563eb; color: white; border: 0;
             border-radius: 6px; cursor: pointer; }
    button:disabled { background: #94a3b8; cursor: wait; }
    #log { margin-top: 20px; white-space: pre-wrap;
           font-family: monospace; font-size: 13px;
           background: #f1f5f9; padding: 12px; border-radius: 6px;
           min-height: 60px; }
  </style>
</head>
<body>
  <h1>YouTube → MP3</h1>
  <input id="url" placeholder="https://www.youtube.com/watch?v=..." autofocus>
  <button id="go">Convert</button>
  <div id="log">Idle.</div>

  <script>
    const urlEl = document.getElementById('url');
    const btn = document.getElementById('go');
    const log = document.getElementById('log');

    btn.onclick = async () => {
      const url = urlEl.value.trim();
      if (!url) return;
      btn.disabled = true;
      log.textContent = 'Starting…';

      try {
        const res = await fetch('/convert', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ url })
        });
        const data = await res.json();
        log.textContent = data.message || JSON.stringify(data, null, 2);
      } catch (e) {
        log.textContent = 'Error: ' + e.message;
      } finally {
        btn.disabled = false;
      }
    };

    urlEl.addEventListener('keydown', e => {
      if (e.key === 'Enter') btn.click();
    });
  </script>
</body>
</html>
"""

@app.get("/")
def index():
    return render_template_string(PAGE)

@app.post("/convert")
def convert():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url.startswith(("http://", "https://")):
        return jsonify(ok=False, message="Please paste a full URL."), 400

    # Important: pass args as a list (no shell=True) — prevents
    # command injection from the URL string.
    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--no-playlist",
        "-o", str(OUTPUT_DIR / "%(title)s.%(ext)s"),
        "--print", "after_move:filepath",   # prints final path on stdout
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return jsonify(ok=False, message="Timed out after 10 minutes."), 500

    if result.returncode != 0:
        return jsonify(
            ok=False,
            message="yt-dlp failed:\n" + (result.stderr or "")[-2000:],
        ), 500

    final_path = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "?"
    return jsonify(
        ok=True,
        message=f"Saved to:\n{final_path}\n\nTotal files in {OUTPUT_DIR}: "
                f"{len(list(OUTPUT_DIR.glob('*.mp3')))}",
    )

if __name__ == "__main__":
    print(f"MP3s will be saved to: {OUTPUT_DIR}")
    print("Open http://127.0.0.1:5000 in your browser. Ctrl+C to stop.")
    # Bind to localhost only — not reachable from your network.
    app.run(host="127.0.0.1", port=5000, debug=False)