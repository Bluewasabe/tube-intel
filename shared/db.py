import sqlite3
import json
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_db(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            youtube_url TEXT NOT NULL,
            video_id TEXT UNIQUE NOT NULL,
            title TEXT,
            channel_name TEXT,
            channel_id TEXT,
            thumbnail_url TEXT,
            published_at TEXT,
            transcript TEXT,
            source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            fail_reason TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER UNIQUE NOT NULL REFERENCES videos(id),
            summary TEXT,
            category TEXT,
            relevant_projects TEXT,
            recommendation TEXT,
            confidence TEXT,
            analyzed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS watched_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT UNIQUE NOT NULL,
            channel_name TEXT,
            channel_url TEXT,
            check_interval_hours INTEGER NOT NULL DEFAULT 12,
            last_checked_at TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            added_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def insert_video(db_path, youtube_url, video_id, title, channel_name,
                 channel_id, thumbnail_url, published_at, source) -> int | None:
    """Insert a video row. Returns row id, or None if video_id already exists."""
    with get_conn(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        if existing:
            return None
        cur = conn.execute(
            """INSERT INTO videos
               (youtube_url, video_id, title, channel_name, channel_id,
                thumbnail_url, published_at, source, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,'pending',?)""",
            (youtube_url, video_id, title, channel_name, channel_id,
             thumbnail_url, published_at, source, _now())
        )
        return cur.lastrowid


def get_video_by_video_id(db_path, video_id) -> dict | None:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return dict(row) if row else None


def get_video_by_id(db_path, vid_id) -> dict | None:
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (vid_id,)).fetchone()
        return dict(row) if row else None


def update_video_status(db_path, video_id, status, fail_reason=None, transcript=None) -> bool:
    """Returns True if the row was found and updated, False if video_id not found."""
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE videos SET status=?, fail_reason=?, transcript=? WHERE video_id=?",
            (status, fail_reason, transcript, video_id)
        )
        return cur.rowcount > 0


def get_pending_video(db_path) -> dict | None:
    """Return the oldest pending video, or None."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM videos WHERE status='pending' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def insert_analysis(db_path, video_id_fk, summary, category, relevant_projects,
                    recommendation, confidence):
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO analysis
               (video_id, summary, category, relevant_projects, recommendation, confidence, analyzed_at)
               VALUES (?,?,?,?,?,?,?)""",
            (video_id_fk, summary, category, json.dumps(relevant_projects),
             recommendation, confidence, _now())
        )


def get_analysis_by_video_id(db_path, video_id_fk) -> dict | None:
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM analysis WHERE video_id = ?", (video_id_fk,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        if result.get("relevant_projects"):
            result["relevant_projects"] = json.loads(result["relevant_projects"])
        return result


def list_videos(db_path, limit=50, offset=0, category=None, source=None, keyword=None) -> list:
    with get_conn(db_path) as conn:
        clauses, params = [], []
        if category:
            clauses.append("a.category = ?"); params.append(category)
        if source:
            clauses.append("v.source = ?"); params.append(source)
        if keyword:
            clauses.append("(v.title LIKE ? OR a.summary LIKE ?)")
            params += [f"%{keyword}%", f"%{keyword}%"]
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params += [limit, offset]
        # SAFETY: `where` only ever contains hardcoded clause strings ("a.category = ?", etc.)
        # User-supplied values go through parameterized `?` bindings in `params` only.
        # Never interpolate user input into `where` directly.
        rows = conn.execute(f"""
            SELECT v.*, a.summary, a.category, a.relevant_projects,
                   a.recommendation, a.confidence, a.analyzed_at
            FROM videos v
            LEFT JOIN analysis a ON a.video_id = v.id
            {where}
            ORDER BY v.created_at DESC
            LIMIT ? OFFSET ?
        """, params).fetchall()
        results = [dict(r) for r in rows]
        for r in results:
            if r.get("relevant_projects"):
                try:
                    r["relevant_projects"] = json.loads(r["relevant_projects"])
                except (json.JSONDecodeError, TypeError):
                    r["relevant_projects"] = []
        return results


def insert_channel(db_path, channel_id, channel_name, channel_url, check_interval_hours=12):
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO watched_channels
               (channel_id, channel_name, channel_url, check_interval_hours, enabled, added_at)
               VALUES (?,?,?,?,1,?)""",
            (channel_id, channel_name, channel_url, check_interval_hours, _now())
        )


def get_enabled_channels(db_path) -> list:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM watched_channels WHERE enabled = 1"
        ).fetchall()
        return [dict(r) for r in rows]


def list_all_channels(db_path) -> list:
    """Return all channels including disabled — used by the management UI."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM watched_channels ORDER BY added_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def update_channel_last_checked(db_path, channel_id):
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE watched_channels SET last_checked_at=? WHERE channel_id=?",
            (_now(), channel_id)
        )


def toggle_channel(db_path, channel_id, enabled: bool):
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE watched_channels SET enabled=? WHERE channel_id=?",
            (1 if enabled else 0, channel_id)
        )


def delete_channel(db_path, channel_id):
    with get_conn(db_path) as conn:
        conn.execute("DELETE FROM watched_channels WHERE channel_id=?", (channel_id,))


def update_channel_interval(db_path, channel_id, hours: int):
    if hours not in (8, 12, 24):
        raise ValueError(f"check_interval_hours must be 8, 12, or 24; got {hours}")
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE watched_channels SET check_interval_hours=? WHERE channel_id=?",
            (hours, channel_id)
        )
