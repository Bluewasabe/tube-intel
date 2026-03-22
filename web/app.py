import os
import re
import sys
from flask import Flask, jsonify, request, render_template

# shared/ is COPY'd into the web container at build time.
# For local dev, the path insert below makes it importable from the project root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from shared.db import (
    init_db, insert_video, get_video_by_video_id, get_video_by_id,
    list_videos, get_analysis_by_video_id, insert_channel, list_all_channels,
    toggle_channel, delete_channel, update_channel_interval
)

DB_PATH = os.environ.get("DB_PATH", "/data/tubeintel.db")
VALID_INTERVALS = {8, 12, 24}

# Matches standard and short YouTube URLs, extracts the 11-char video ID
_YT_RE = re.compile(r'(?:youtube\.com/watch\?.*v=|youtu\.be/)([a-zA-Z0-9_-]{11})')


def extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from a URL. Returns None if not a valid YouTube URL."""
    m = _YT_RE.search(url)
    return m.group(1) if m else None


def create_app(db_path: str = None) -> Flask:
    """App factory — accepts db_path override for testing."""
    app = Flask(__name__, template_folder="templates", static_folder="static")
    _db = db_path or DB_PATH
    init_db(_db)

    # ── Health ────────────────────────────────────────────────────────────────

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    # ── Video submission ──────────────────────────────────────────────────────

    @app.post("/api/submit")
    def api_submit():
        """Queue a YouTube URL for analysis.

        Body: {"url": str, "source": "manual"|"discord"|"scheduled"}
        Returns: {"video_id": str, "status": "queued"|"exists", "id": int}
        """
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        source = data.get("source", "manual")

        if not url:
            return jsonify({"error": "url required"}), 400

        video_id = extract_video_id(url)
        if not video_id:
            return jsonify({"error": "not a valid YouTube URL"}), 400

        existing = get_video_by_video_id(_db, video_id)
        if existing:
            return jsonify({"video_id": video_id, "status": "exists", "id": existing["id"]})

        row_id = insert_video(
            _db, url, video_id,
            title=None, channel_name=None, channel_id=None,
            thumbnail_url=None, published_at=None, source=source
        )
        return jsonify({"video_id": video_id, "status": "queued", "id": row_id})

    # ── Video listing & detail ────────────────────────────────────────────────

    @app.get("/api/videos")
    def api_videos():
        """Paginated video list with optional filters.

        Query params: limit (max 100), offset, category, source, q (keyword)
        Returns: {"videos": [...], "total": int, "offset": int}
        """
        limit = min(int(request.args.get("limit", 50)), 100)
        offset = int(request.args.get("offset", 0))
        category = request.args.get("category")
        source = request.args.get("source")
        keyword = request.args.get("q")
        rows = list_videos(_db, limit=limit, offset=offset,
                           category=category, source=source, keyword=keyword)
        return jsonify({"videos": rows, "total": len(rows), "offset": offset})

    @app.get("/api/video/<int:vid_id>")
    def api_video_detail(vid_id):
        """Full video record + analysis for one video.

        Returns: {"video": {...}, "analysis": {...}|null}
        """
        video = get_video_by_id(_db, vid_id)
        if not video:
            return jsonify({"error": "not found"}), 404
        analysis = get_analysis_by_video_id(_db, vid_id)
        return jsonify({"video": video, "analysis": analysis})

    # ── Channel management ────────────────────────────────────────────────────

    @app.get("/api/channels")
    def api_list_channels():
        """All watched channels (enabled + disabled) for the management UI."""
        channels = list_all_channels(_db)
        return jsonify({"channels": channels})

    @app.post("/api/channels")
    def api_add_channel():
        """Add a channel to the watch list.

        Body: {"channel_id": str, "channel_name": str, "channel_url": str,
               "check_interval_hours": 8|12|24}
        """
        data = request.get_json(silent=True) or {}
        channel_id = (data.get("channel_id") or "").strip()
        channel_name = (data.get("channel_name") or "").strip()
        channel_url = (data.get("channel_url") or "").strip()

        try:
            interval = int(data.get("check_interval_hours", 12))
        except (TypeError, ValueError):
            interval = 12

        if interval not in VALID_INTERVALS:
            return jsonify({"error": "interval must be 8, 12, or 24"}), 400
        if not channel_id:
            return jsonify({"error": "channel_id required"}), 400

        insert_channel(_db, channel_id, channel_name, channel_url, interval)
        return jsonify({"ok": True})

    @app.delete("/api/channels/<channel_id>")
    def api_delete_channel(channel_id):
        """Remove a channel from the watch list."""
        delete_channel(_db, channel_id)
        return jsonify({"ok": True})

    @app.patch("/api/channels/<channel_id>")
    def api_patch_channel(channel_id):
        """Update channel enabled state or check interval.

        Body: {"enabled": bool} and/or {"check_interval_hours": 8|12|24}
        """
        data = request.get_json(silent=True) or {}
        if "enabled" in data:
            toggle_channel(_db, channel_id, bool(data["enabled"]))
        if "check_interval_hours" in data:
            try:
                hours = int(data["check_interval_hours"])
            except (TypeError, ValueError):
                return jsonify({"error": "invalid interval"}), 400
            if hours not in VALID_INTERVALS:
                return jsonify({"error": "interval must be 8, 12, or 24"}), 400
            update_channel_interval(_db, channel_id, hours)
        return jsonify({"ok": True})

    # ── Page routes (templates served by Flask) ───────────────────────────────

    @app.get("/")
    def feed():
        return render_template("feed.html")

    @app.get("/video/<int:vid_id>")
    def video_detail(vid_id):
        return render_template("video.html", vid_id=vid_id)

    @app.get("/submit")
    def submit_page():
        return render_template("submit.html")

    @app.get("/channels")
    def channels_page():
        return render_template("channels.html")

    return app


if __name__ == "__main__":
    port = int(os.environ.get("FLASK_PORT", 5090))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    create_app().run(host="0.0.0.0", port=port, debug=debug)
