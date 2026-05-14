# TubeIntel

Self-hosted YouTube intelligence service. Submit a YouTube URL (manually, via Discord, or via scheduled channel polling) and TubeIntel fetches the transcript, sends it to Claude for analysis against your active projects and goals, stores the result in SQLite, and posts a Discord notification with the category, confidence, and a link to the full analysis in the web dashboard.

---

## 1. User Focus

### What it does

YouTube is full of videos worth your attention — and most of them slip past you, get bookmarked, and forgotten. TubeIntel watches your channels and your inbox for new videos, asks Claude whether each one is relevant to the projects you actually care about, and routes the interesting ones to a dashboard and a Discord channel.

For each video, you get:
- A 2–3 sentence summary
- A category (`new_project`, `apply_to_existing`, `learning`, `homelab`, `velvet_verve`, `low_value`)
- The specific projects from your context that this video applies to
- A concrete recommendation ("apply this to plexboy ZFS migration, see 0:14:32")
- A confidence rating (`high` / `medium` / `low`)

### Getting started

**Prerequisites:**
- Docker and Docker Compose
- An Anthropic API key
- (Optional) A Discord bot token and webhook URL for inbound submissions / outbound notifications

**Install:**

```bash
git clone https://github.com/Bluewasabe/tube-intel.git
cd tube-intel
cp .env.example .env
```

**Configure** `.env`:

```env
ANTHROPIC_API_KEY=sk-ant-...
DISCORD_BOT_TOKEN=          # Optional — leave blank to disable Discord submissions
DISCORD_SUBMIT_CHANNEL_ID=  # Numeric channel ID of #yt-submit
DISCORD_WEBHOOK_URL=        # Outbound webhook for #yt-intel notifications
FLASK_PORT=5090
FLASK_DEBUG=false
DB_PATH=/data/tubeintel.db
```

**Edit `prompt_context.md`** to describe your active projects and goals. This is the context Claude uses to categorize each video. The file is bind-mounted read-only into both containers, so you can edit it on the host and the worker picks up changes on its next run — no rebuild needed.

**Run:**

```bash
docker compose up -d --build
```

Web dashboard is at `http://localhost:5090`. Health check at `http://localhost:5090/health` returns `{"ok": true}`.

### How to use it

There are three ways to feed videos in:

1. **Manual submit** — open `http://localhost:5090/submit`, paste a YouTube URL, click Analyze.
2. **Discord** — post a YouTube URL in your `#yt-submit` channel. The bot reacts with ✅ and replies with the queued title. Results land in `#yt-intel`.
3. **Channel polling** — add a YouTube channel under `/channels`. TubeIntel checks its RSS feed every 8 / 12 / 24 hours and queues any new uploads automatically.

The feed at `/` shows everything analyzed, filterable by category, source, keyword, and project name. Filter values are persisted in the URL — bookmark a filtered view (e.g., "homelab + high confidence") and share it.

### Common gotchas

- **No transcript = no analysis.** TubeIntel uses `youtube-transcript-api`. Videos without auto-captions or with disabled transcripts will fail with `fail_reason="no_transcript"`. Auto-generated captions count.
- **The Discord bot needs the `MESSAGE CONTENT` privileged intent** enabled in the Discord developer portal. Without it, the bot silently ignores all messages.
- **`DISCORD_SUBMIT_CHANNEL_ID` must be the numeric ID**, not a channel name. Enable Developer Mode in Discord settings, right-click the channel, "Copy Channel ID."
- **Don't expose the public endpoint without auth.** TubeIntel ships with no authentication. If you bind it to a public hostname (e.g., via a reverse proxy), put it behind Authentik, an IP allowlist, or a shared-secret header first. Every submit costs Claude API credits.
- **`prompt_context.md` is read at runtime,** not at build. Edit on the host, then the next worker job uses the new context. No rebuild needed.
- **Re-analyze is not yet a feature.** To re-run analysis on a video, manually delete its row from the `analysis` table; the worker will pick it up on the next 30s cycle.

---

## 2. Developer Focus

### Architecture

Two Docker containers, one SQLite volume:

```
tube-intel/
├── web/              → Flask dashboard + REST API (port 5090, threaded)
├── worker/           → APScheduler + Discord bot + Claude pipeline (asyncio, no port)
├── shared/           → db.py — COPY'd into both images at build time
├── data/             → gitignored — SQLite volume mount at /data/tubeintel.db
├── prompt_context.md → mounted read-only into both containers, updated without rebuild
├── tests/            → pytest, 62 tests, isolated tmp_path DB per test
├── docker-compose.yml
└── .env              → secrets (never committed)
```

**Why two containers:** `discord.py` runs on asyncio; Flask runs threaded. Combining them risks event-loop conflict and deadlock. Splitting them also means the dashboard stays up if the worker crashes.

**Why a shared module:** `shared/db.py` is the SQLite layer used by both the web container (for API CRUD) and the worker (for pipeline writes). Docker can't reference paths outside the build context, so both Dockerfiles use the repo root as context (`context: .`) and `COPY shared/ ./shared/`. Runtime data is shared via the `./data` volume mount.

### File map

| Path | Purpose |
|------|---------|
| `shared/db.py` | SQLite schema init, all CRUD helpers, WAL mode, JSON (de)serialization for `relevant_projects` |
| `web/app.py` | Flask app factory + 4 page routes + 6 API routes + 1 health route |
| `web/requirements.txt` | `flask>=3.0` only |
| `web/templates/` | `base.html`, `feed.html`, `video.html`, `submit.html`, `channels.html` |
| `web/static/` | `style.css` (design tokens + components), `app.js` (shared client helpers) |
| `worker/worker.py` | Entrypoint — boots scheduler, Discord bot, keeps event loop alive |
| `worker/pipeline.py` | The full per-video pipeline: metadata → transcript → Claude → DB → Discord |
| `worker/scheduler.py` | APScheduler jobs: pending-video processor (30s) + per-channel RSS polling (8/12/24h) |
| `worker/discord_bot.py` | Inbound submission bot for `#yt-submit` |
| `worker/requirements.txt` | discord.py, apscheduler, httpx, anthropic, youtube-transcript-api, feedparser |
| `tests/` | `test_db.py`, `test_web_api.py`, `test_pipeline.py`, `test_scheduler.py`, `test_notifier.py` |
| `prompt_context.md` | Your active projects + goals — Claude reads this at every pipeline run |
| `.env.example` | All required env vars with empty values |
| `docker-compose.yml` | Build + run config for both containers, healthcheck, log rotation |

### Key design decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| DB engine | SQLite + WAL mode | No network DB overhead. WAL allows concurrent reads from web + writes from worker without lock contention. WAL pragma set in **both** `init_db` and `get_conn`. |
| Shared DB layer | `shared/db.py` COPY'd at build time | Docker can't reference paths outside the build context — bake it in. Runtime data shared via volume mount. |
| Two containers | web (Flask) + worker (asyncio) | asyncio + threaded Flask in one process is a deadlock risk. Splitting them also gives independent failure domains. |
| Port | 5090 | Avoids SIP port 5060 conflict on most home networks. |
| YouTube metadata | oEmbed (no API key) | Lightweight, no quota, no key. Trade-off: limited fields (title, channel name, thumbnail only — no published_at). |
| Transcript fetch | `youtube-transcript-api` | No API key required. Trade-off: brittle to YouTube anti-bot changes; runs better on residential IPs than VPS. |
| Claude model | `claude-sonnet-4-6`, async client | Best balance of analysis quality vs cost. Async lets the worker process the next job while one is in-flight. |
| Analysis output | Strict JSON schema | Deterministic parsing. On parse failure, the pipeline retries once with a stricter "JSON only, no markdown" prompt before giving up. |
| Rate limiting | 1 video per 30s job cycle | Avoids hammering the Claude API and the YouTube transcript endpoint. |
| Channel polling | YouTube RSS feed (no API key) | `https://www.youtube.com/feeds/videos.xml?channel_id={id}` via feedparser. No quota, no key. |
| Auth | None (v1) | Internal homelab scope. Add Authentik / NPM ACL / shared-secret header before public exposure. |
| Failure mode codes | Distinct `fail_reason` strings | `rate_limited` vs `claude_error` vs `parse_error` vs `no_transcript` vs `fetch_error` vs `db_error`. Makes log triage tractable. |

### Database schema

Three tables in `/data/tubeintel.db`:

- **`videos`** — one row per YouTube video. `video_id` UNIQUE prevents duplicate scans. `status` tracks pipeline state (`pending` → `processing` → `done` | `failed`).
- **`analysis`** — one-to-one with `videos` via foreign key. `relevant_projects` stored as a JSON array string, deserialized on read.
- **`watched_channels`** — `check_interval_hours` is enum-constrained to `8` / `12` / `24` at both the API and DB layers. `enabled` is `0` / `1`.

### API routes

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Healthcheck — returns `{"ok": true}` |
| POST | `/api/submit` | Queue a YouTube URL — body: `{url, source}` |
| GET | `/api/videos` | Paginated video list — params: `limit`, `offset`, `category`, `source`, `q`, `project` |
| GET | `/api/video/<id>` | Full video record + analysis |
| GET | `/api/channels` | List all watched channels |
| POST | `/api/channels` | Add a channel — body: `{channel_url, channel_name?, check_interval_hours}` |
| DELETE | `/api/channels/<channel_id>` | Remove a channel |
| PATCH | `/api/channels/<channel_id>` | Toggle enabled or update interval |
| GET | `/` | Feed page |
| GET | `/video/<id>` | Video detail page |
| GET | `/submit` | Submit form page |
| GET | `/channels` | Channel management page |

### Running tests

```bash
cd c:/Users/Bluew/Code/tube-intel
python -m pytest tests/ -q
```

Tests use `tmp_path` fixtures and the `create_app(db_path=...)` factory so every test gets an isolated DB.

### What requires a rebuild

| Change | Rebuild needed? |
|--------|----------------|
| `web/app.py` or `shared/db.py` | Yes — `docker compose up -d --build` |
| `worker/` Python files | Yes — `docker compose up -d --build` |
| `.env` changes | No — loaded at container start via `env_file` |
| `prompt_context.md` | No — mounted as a read-only volume; worker picks up on next run |
| `web/templates/` or `web/static/` | Yes (baked into image). Add a bind mount during dev to skip rebuilds. |

### Known limitations

- **No authentication.** Suitable for an internal homelab network only. Do not expose `/api/submit` to the open internet without an auth gate.
- **No API-edge rate limiting.** The worker is rate-limited to 1/30s, but the API will queue thousands of submissions in seconds if hammered.
- **DB connections are not explicitly closed.** `with get_conn(...) as conn:` commits/rolls back but does not close. Long-running worker may accumulate connections until GC. Wrap in `contextlib.closing()` to fix.
- **Transcripts depend on `youtube-transcript-api`,** which is brittle to YouTube anti-bot changes. Runs better on residential IPs than VPS.
- **No re-analyze UI.** To re-run analysis, delete the row from the `analysis` table by hand.
- **`yt-dlp` is in `worker/requirements.txt` but unused.** Safe to remove.
- **Discord bot has no per-user rate limit.** Channel ACL is the only gate.

---

## 3. Enhancement Ideas

The following are possibilities for future sessions or contributors. None are committed and there are no timelines.

### Hardening
- Auth gate via Authentik forward-auth on NPM, IP allowlist, or shared-secret header on `/api/*`
- API-edge rate limit with `Flask-Limiter` (e.g., 10/min/IP)
- Wrap DB connections in `contextlib.closing()` to close them deterministically
- Structured JSON logging with `video_id` correlation field for log ingestion
- Startup assertions that log which API keys are present / missing

### UX
- Re-analyze button on the video detail page (drop analysis row, set `status='pending'`)
- "Mark as viewed" — read/unread visual indicator
- Pinned category color legend in the nav
- Hide channel ID by default in the channels UI (show on hover or last 6 chars only)
- Wider desktop layout (currently capped at 680px) or a 2-column variant at ≥1200px

### Features
- Daily / weekly Discord digest at a chosen hour summarizing new analyses by category
- Manual category override that persists, with optional feedback into future Claude prompts
- Apply-to-project deeplinks — auto-generate a markdown summary when category=`apply_to_existing`
- Bulk channel import from a YouTube subscriptions OPML or a comma-separated list
- Whisper local fallback for transcripts when `youtube-transcript-api` is blocked
- Transcript full-text search via SQLite FTS5

### Bigger swings
- "Ask TubeIntel" — vector store over transcripts + analyses, Claude-powered Q&A with timestamp citations
- Browser extension — right-click on a YouTube page to submit
- Knowledge graph view — videos as nodes, edges weighted by shared `relevant_projects`
- Productize as a hosted service / curated newsletter
