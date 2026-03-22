# CLAUDE.md — TubeIntel

Global rules (git workflow, methodology, shell env, GitHub account) live in `~/.claude/CLAUDE.md`.

---

## Current Build State — 2026-03-22

**Status: Phase 1 ✅ | Phase 2 ✅ | Phase 3 ✅ (Worker + Discord) | Phase 4 ⬜ (Frontend) | Phase 5 ⬜ (Deploy)**

**Not yet live.** No containers built, no LXC provisioned, no NPM proxy configured.

| File | Done | Phase |
|------|------|-------|
| `PLAN.md` | ⬜ (not yet created) | — |
| `CLAUDE.md` | ✅ | — |
| `.env.example` | ✅ | 1 |
| `.gitignore` | ✅ | 1 |
| `prompt_context.md` | ✅ | 1 |
| `shared/db.py` | ✅ | 1 |
| `tests/test_db.py` | ✅ | 1 |
| `web/app.py` | ✅ | 2 |
| `web/requirements.txt` | ✅ | 2 |
| `tests/test_web_api.py` | ✅ | 2 |
| Phase 3 | ✅ | Analysis pipeline (`worker/pipeline.py`) — extract, Claude, notify |
| `web/Dockerfile` | ⬜ | 3 |
| `worker/worker.py` | ⬜ | 3 |
| `worker/pipeline.py` | ⬜ | 3 |
| `worker/discord_bot.py` | ⬜ | 3 |
| `worker/scheduler.py` | ⬜ | 3 |
| `worker/requirements.txt` | ⬜ | 3 |
| `worker/Dockerfile` | ⬜ | 3 |
| `web/templates/` | ⬜ | 4 |
| `web/static/` | ⬜ | 4 |
| `docker-compose.yml` | ⬜ | 5 |

---

## Project Summary

TubeIntel is a self-hosted YouTube video intelligence service. Submit a YouTube URL (manually, via Discord, or via scheduled channel polling) and it fetches the transcript, sends it to Claude for analysis, stores the result in SQLite, and posts a Discord notification with the category, confidence, and a link to the full analysis in the web dashboard.

**Problem solved:** YouTube videos with useful content get forgotten. TubeIntel captures, categorizes, and routes them to where they belong.

Stack: Python Flask (web) + asyncio worker (Discord bot + APScheduler + Claude pipeline), SQLite with WAL mode, Docker Compose. Deployed on a Proxmox LXC, exposed via NPM at `youtube-intel.bookclub44.com`.

---

## Architecture

Two Docker containers, one SQLite volume:

```
tube-intel/
├── web/          → Flask dashboard + REST API (port 5090)
├── worker/       → APScheduler + Discord bot + Claude pipeline (no port)
├── shared/       → db.py — COPY'd into both images at build time
├── data/         → gitignored — SQLite volume mount at /data/tubeintel.db
└── prompt_context.md  → mounted read-only into both containers, updated without rebuild
```

**Why two containers:** `discord.py` runs on asyncio; Flask runs threaded. Combining them risks deadlock. Web stays fast and independent — if worker crashes, dashboard still loads.

**Shared module pattern:** Both Dockerfiles use the repo root as build context (not the service subdirectory). This allows `COPY shared/ ./shared/` to work. In `docker-compose.yml`, set `context: .` and `dockerfile: web/Dockerfile` (or `worker/Dockerfile`).

---

## Key Design Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| DB engine | SQLite + WAL mode | No network DB overhead; WAL handles concurrent reads from web + writes from worker without lock contention |
| Shared DB layer | `shared/db.py` COPY'd at build time | Docker can't reference paths outside build context — bake it in; runtime data shared via volume |
| Port | 5090 | Avoids SIP port 5060 conflict |
| YouTube metadata | yt-dlp (metadata-only) | No YouTube API key required |
| Transcript fetch | youtube-transcript-api | No API key required |
| Claude model | claude-sonnet-4-6, async | Best balance of analysis quality vs cost |
| Analysis output | Strict JSON schema | Deterministic parsing; retry with stricter prompt on parse failure |
| Auth | None (v1) | Internal homelab network; Authentik can be added later |
| Rate limiting | 1 video per 30s job cycle | Avoid Claude API hammering |
| Channel polling | YouTube RSS feed (no API key) | `https://www.youtube.com/feeds/videos.xml?channel_id={id}` via feedparser |

---

## File Map

| Path | Purpose |
|------|---------|
| `shared/db.py` | SQLite schema init, all CRUD helpers, WAL mode, JSON (de)serialization for `relevant_projects` |
| `web/app.py` | Flask app factory + 10 routes (4 page routes, 6 API routes) |
| `web/requirements.txt` | `flask>=3.0` only — shared/db.py has no extra deps |
| `tests/test_db.py` | DB layer unit tests (tmp_path fixtures, isolated per test) |
| `tests/test_web_api.py` | Flask API integration tests (13 test cases, tmp_path, create_app factory) |
| `prompt_context.md` | Chris's current projects + goals — read by pipeline.py at runtime; update here, no rebuild |
| `.env.example` | All required env vars with empty values |

---

## API Routes (Phase 2 — all implemented)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Healthcheck — returns `{"ok": true}` |
| POST | `/api/submit` | Queue a YouTube URL — body: `{url, source}` |
| GET | `/api/videos` | Paginated video list — params: `limit, offset, category, source, q` |
| GET | `/api/video/<id>` | Full video record + analysis |
| GET | `/api/channels` | List all watched channels |
| POST | `/api/channels` | Add a channel — body: `{channel_id, channel_name, channel_url, check_interval_hours}` |
| DELETE | `/api/channels/<channel_id>` | Remove a channel |
| PATCH | `/api/channels/<channel_id>` | Toggle enabled or update interval |
| GET | `/` | Feed page (template) |
| GET | `/video/<id>` | Video detail page (template) |
| GET | `/submit` | Submit form page (template) |
| GET | `/channels` | Channel management page (template) |

---

## Data Model

Three tables in `/data/tubeintel.db`:

- **`videos`** — one row per YouTube video; `video_id` UNIQUE prevents duplicate scans; `status` tracks pipeline state (`pending` → `processing` → `done` | `failed`)
- **`analysis`** — one-to-one with videos via FK; `relevant_projects` stored as JSON array string, deserialized on read
- **`watched_channels`** — `check_interval_hours` enum-constrained to 8/12/24 at the API layer; `enabled` is 0/1

---

## Environment Variables

```env
# Claude API
ANTHROPIC_API_KEY=

# Discord
DISCORD_BOT_TOKEN=          # Bot token (inbound listener in #yt-submit)
DISCORD_SUBMIT_CHANNEL_ID=  # ID of #yt-submit channel
DISCORD_WEBHOOK_URL=        # Webhook URL for outbound notifications to #yt-intel

# App
FLASK_PORT=5090
FLASK_DEBUG=false

# Database
DB_PATH=/data/tubeintel.db
```

Note: `DB_PATH` has a hardcoded default of `/data/tubeintel.db` in `web/app.py`. The `.env` value overrides it — keep them consistent.

---

## What Requires a Rebuild

| Change | Rebuild needed? |
|--------|----------------|
| `web/app.py` or `shared/db.py` | Yes — `docker compose up -d --build` |
| `worker/` Python files | Yes — `docker compose up -d --build` |
| `.env` changes | No — loaded at container start via `env_file` |
| `prompt_context.md` | No — mounted as read-only volume; edit on host, worker picks up on next run |
| `web/templates/` or `web/static/` | Yes (baked into image) — add a bind mount during dev to skip rebuilds |

---

## Safe to Commit

- `shared/db.py`, `web/app.py`, `web/requirements.txt`
- `worker/` Python files (when built)
- `tests/`
- `docker-compose.yml`, `web/Dockerfile`, `worker/Dockerfile`
- `prompt_context.md`
- `PLAN.md`, `CLAUDE.md`, `README.md`
- `.env.example`, `.gitignore`

**Never commit:** `.env`, `data/` (SQLite lives here), `*.log`

---

## Deployment Target

| Setting | Value |
|---------|-------|
| Host | New Proxmox LXC on pve (not yet provisioned) |
| Port | 5090 (web container) |
| Exposed via | NPM on nginx-vm (192.168.1.106) |
| Public URL | `youtube-intel.bookclub44.com` |
| Docker network | Isolated per-stack (no shared `jarvis-net`) |

Healthcheck path for NPM / Docker: `GET /health` → 200 `{"ok": true}`

---

## Phase 3 — Pipeline Key Notes

- `fetch_transcript()` is synchronous (youtube-transcript-api has no async API) — always call via `run_in_executor` to avoid blocking the event loop
- `DISCORD_WEBHOOK_URL` is read from `os.environ` inside each notify function (not at module level) so env changes at runtime take effect without restart
- `RateLimitExhausted` is a distinct sentinel exception so `run_pipeline` can set `fail_reason=rate_limited` vs generic `claude_error`
- `post_discord_success` accepts `relevant_projects` as either a Python list or a JSON string — handles both safely
- `prompt_context.md` is read from `/app/prompt_context.md` at runtime (mounted read-only volume) — update on host, no rebuild needed

---

## Lessons Learned

### Phase 1 (DB Layer)
- WAL mode must be set in both `init_db` and `get_conn` — `init_db` uses a bare connection that doesn't go through `get_conn`, so the pragma has to be set explicitly in both.
- `relevant_projects` is stored as a JSON string in SQLite. Deserialize it on every read path (`get_analysis_by_video_id`, `list_videos`). Wrap `json.loads` in try/except — don't let a corrupt row blow up a list query.
- `insert_video` returns `None` (not an exception) when `video_id` already exists — the dedup check is an explicit `SELECT` before `INSERT`, not relying on the UNIQUE constraint to throw. Callers must handle `None`.
- `update_channel_interval` validates the hours value at the DB layer too (raises `ValueError`) as a secondary guard. The API layer validates first with `VALID_INTERVALS`.
- `list_videos` and `count_videos` use f-string WHERE clauses, but only with hardcoded clause strings — user values always go through `?` params. The safety comment was added explicitly because this pattern looks like a SQL injection risk at a glance.

### Phase 2 (Flask API)
- App factory pattern (`create_app(db_path=None)`) is essential for testing — lets tests inject a tmp_path DB without environment variable side effects.
- `VALID_INTERVALS = {8, 12, 24}` (a set, not a list) — `in` membership check is O(1) and the intent is clear.
- `PATCH /api/channels/<channel_id>` requires at least one of `enabled` or `check_interval_hours` — returns 400 if body is empty or contains neither. This guard was missing in the initial commit and added in the fix commit.
- `GET /api/videos` total reflects the full filtered count, not just the current page size. This was a bug in the first commit (`total` was `len(rows)`) — fixed by calling `count_videos` separately.
- `request.get_json(silent=True) or {}` pattern throughout — never crashes on missing or malformed JSON body.
- The `health` route has no docstring by design — it's self-evident. All 6 API routes have docstrings.
- `curl` will need to be in the web Dockerfile for the Docker healthcheck — `python:3.12-slim` doesn't include it (lesson from FIBI Phase 3).
