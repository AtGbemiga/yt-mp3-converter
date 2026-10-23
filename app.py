import os
import subprocess
import uuid
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string, send_file
from yyds_mdns import MDNS
from ytmusic_routes import ytmusic_bp

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "orin-dev-secret-change-in-prod")
app.register_blueprint(ytmusic_bp)
MDNS(app, name="flask-app", port=5000)

OUTPUT_DIR = Path.home() / "Music" / "yt-mp3"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

JOBS = {}

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
           background: #f1f5f9; color: #0f172a; padding: 12px;
           border-radius: 6px; min-height: 60px; }
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

    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "0",
        "--no-playlist",
        "-o", str(OUTPUT_DIR / "%(title)s.%(ext)s"),
        "--print", "after_move:filepath",
        url,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return jsonify(ok=False, message="Timed out."), 500

    if result.returncode != 0:
        return jsonify(ok=False, message="yt-dlp failed:\n" + result.stderr[-2000:]), 500

    final_path = result.stdout.strip().splitlines()[-1]
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"path": final_path, "title": Path(final_path).name}

    return jsonify(
        ok=True,
        job_id=job_id,
        title=Path(final_path).name,
        message=f"Saved to: {final_path}",
    )

@app.get("/download/<job_id>")
def download(job_id):
    job = JOBS.get(job_id)
    if not job or not Path(job["path"]).exists():
        return jsonify(ok=False, message="Job not found"), 404

    return send_file(
        job["path"],
        mimetype="audio/mpeg",
        as_attachment=True,
        download_name=job["title"],
    )

if __name__ == "__main__":
    print(f"MP3s will be saved to: {OUTPUT_DIR}")
    print("Open http://flask-app.local:5000 in your browser. Ctrl+C to stop.")
    app.run(host="0.0.0.0", port=5000, debug=False)
