import os
import sys
import subprocess
import json
import re
from flask import Flask, render_template, request, jsonify, send_file

app = Flask(__name__)

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def get_video_info(url):
    """Extract video info without downloading using yt-dlp."""
    try:
        cmd = [
            sys.executable, "-m", "yt_dlp",
            "--no-download",
            "--dump-json",
            "--no-warnings",
            "--no-check-certificates",
            "--extractor-args", "generic:impersonate",
            url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            err = result.stderr[:500]
            # Provide helpful error for YouTube sign-in requirement
            if "Sign in to confirm" in err or "Use --cookies-from-browser" in err:
                return None, "YouTube requires sign-in to verify you're not a bot. This cannot be bypassed on a server. Try: 1) Use a non-YouTube site, 2) Use Cobalt.tools directly, or 3) Self-host with YouTube cookies."
            # Provide helpful error for Cloudflare/anti-bot
            if "Cloudflare" in err or "403" in err or "impersonat" in err:
                return None, "This site has Cloudflare/anti-bot protection that blocks server requests. Try again later, or use an alternative like Cobalt.tools."
            return None, err
        
        # yt-dlp can return multiple lines (playlists); take the first entry
        lines = result.stdout.strip().split("\n")
        for line in lines:
            line = line.strip()
            if line.startswith("{"):
                data = json.loads(line)
                return data, None
        return None, "No video info returned"
    except subprocess.TimeoutExpired:
        return None, "Request timed out"
    except Exception as e:
        return None, str(e)


def sanitize_filename(name):
    """Remove illegal characters from filename."""
    return re.sub(r'[\\/*?:"<>|]', "", name)[:100]


@app.route("/")
def index():
    adsense_id = os.environ.get("ADSENSE_CLIENT", "REPLACE_ME")
    return render_template("index.html", adsense_id=adsense_id)


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route("/api/info", methods=["POST"])
def api_info():
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    info, err = get_video_info(url)
    if err:
        return jsonify({"error": err}), 400

    # Extract useful fields for the frontend
    title = info.get("title", "Unknown")
    duration = info.get("duration_string", "N/A")
    uploader = info.get("uploader", "N/A")
    thumbnail = info.get("thumbnail", "")
    
    # List available formats
    formats = []
    seen = set()
    for f in info.get("formats", []):
        ext = f.get("ext", "")
        height = f.get("height") or 0
        format_id = f.get("format_id", "")
        format_note = f.get("format_note", "")
        if format_id not in seen:
            seen.add(format_id)
            # Determine resolution label
            if height:
                resolution = f"{height}p"
            elif format_note:
                resolution = format_note
            elif "audio" in format_id.lower() or f.get("acodec") != "none":
                resolution = "audio only"
            else:
                resolution = f"({ext})"
            formats.append({
                "format_id": format_id,
                "ext": ext,
                "resolution": resolution,
                "height": height,
            })
    
    formats.sort(key=lambda x: x["height"], reverse=True)

    return jsonify({
        "title": title,
        "duration": duration,
        "uploader": uploader,
        "thumbnail": thumbnail,
        "formats": formats[:10],  # top 10 quality options
    })


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.json
    url = data.get("url", "").strip()
    format_id = data.get("format_id", "best")
    
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    # Get info first to find a good filename
    info, err = get_video_info(url)
    if err:
        return jsonify({"error": err}), 400

    title = sanitize_filename(info.get("title", "video"))
    out_path = os.path.join(DOWNLOAD_DIR, f"{title}.%(ext)s")

    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", format_id,
        "-o", out_path,
        "--no-warnings",
        "--no-playlist",
        "--no-check-certificates",
        "--extractor-args", "generic:impersonate",
        url
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            return jsonify({"error": result.stderr[:500]}), 400

        # Find the actual downloaded file
        downloaded = None
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(title):
                downloaded = os.path.join(DOWNLOAD_DIR, f)
                break

        if not downloaded:
            return jsonify({"error": "Download completed but file not found"}), 500

        return jsonify({
            "success": True,
            "filename": os.path.basename(downloaded),
            "size_mb": round(os.path.getsize(downloaded) / (1024 * 1024), 1),
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Download timed out (file may be too large)"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/save/<filename>")
def api_save(filename):
    """Let user download the file to their machine."""
    path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(path):
        return "File not found", 404
    return send_file(path, as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
