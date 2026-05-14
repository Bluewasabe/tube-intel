# TubeIntel — Build Plan

Phased development plan. Phases 1–6 are retroactive (the code shipped before the plan was written). Phase 7 is the active phase as of 2026-05-12, gating the first production deploy.

---

## Status

| Phase | Done | Description |
|-------|------|-------------|
| Phase 1 | ✅ | DB layer — schema, CRUD, WAL, JSON serialization |
| Phase 2 | ✅ | Flask web API — 10 routes, app factory, tests |
| Phase 3 | ✅ | Analysis pipeline — metadata, transcript, Claude, Discord notify |
| Phase 4 | ✅ | Worker scheduler + Discord bot |
| Phase 5 | ✅ | Dashboard UI — feed, detail, submit, channels pages |
| Phase 6 | ✅ | Containerization — Dockerfiles, docker-compose.yml |
| Phase 7 | ⬜ | **Local deploy + dogfood — current** (LAN-only, no public exposure) |
| Phase 8 | ⬜ | Public exposure + hardening (auth, rate limit, DNS/NPM) |
| Phase 9 | ⬜ | Daily-use polish (re-analyze, mark-viewed, digests) |
| Phase 10 | ⬜ | Strategic — productize vs. internal-tool decision |

---

## Phase 1 — DB Layer ✅

- [x] `shared/db.py` — schema for `videos`, `analysis`, `watched_channels`
- [x] WAL mode set in both `init_db` and `get_conn`
- [x] All CRUD helpers with parameterized SQL
- [x] `relevant_projects` stored as JSON, deserialized on read
- [x] `tests/test_db.py` — full coverage with `tmp_path` fixtures

**Lesson:** WAL pragma must be set in both `init_db` and `get_conn` — they use different connections.

---

## Phase 2 — Flask Web API ✅

- [x] `web/app.py` — `create_app(db_path=None)` factory
- [x] Health endpoint
- [x] Video submission + listing + detail
- [x] Channel CRUD (POST / GET / PATCH / DELETE)
- [x] Filters: category, source, keyword, project — with proper pagination
- [x] `tests/test_web_api.py` — 13 integration test cases

**Lesson:** `total` in paginated responses must come from a separate `count_videos` call, not `len(rows)`.

---

## Phase 3 — Analysis Pipeline ✅

- [x] `worker/pipeline.py` — metadata fetch via oEmbed
- [x] Transcript fetch via `youtube-transcript-api` (sync, run in executor)
- [x] Claude call with retry-with-backoff on 429
- [x] Strict JSON parsing with retry-with-stricter-prompt on parse failure
- [x] Discord webhook notifications (success + failure variants)
- [x] Distinct `fail_reason` codes per failure mode

**Lesson:** Read `DISCORD_WEBHOOK_URL` from env at call time, not module load — lets runtime env changes take effect without restart.

---

## Phase 4 — Worker Scheduler + Discord Bot ✅

- [x] `worker/worker.py` — single asyncio event loop hosting both scheduler and bot
- [x] `worker/scheduler.py` — 30s job processor + per-channel RSS polling
- [x] `worker/discord_bot.py` — listens in `#yt-submit`, reacts with ✅, posts to `/api/submit`
- [x] Graceful degradation: no bot token = scheduler-only mode
- [x] In-memory fail-count → Discord warning after 3 consecutive RSS failures
- [x] Fire-and-forget catch-up runs at startup so the loop doesn't block

**Lesson:** `DISCORD_SUBMIT_CHANNEL_ID=0` is a valid disabled state, not an error — the bot silently ignores everything.

---

## Phase 5 — Dashboard UI ✅

- [x] `base.html` + nav with mobile hamburger
- [x] Feed page with category/source/keyword/project filters, URL-synced
- [x] Video detail page (client-fetched data)
- [x] Submit form with success/exists/error states
- [x] Channels page — add form, toggle, interval select, delete
- [x] Auto-refresh every 15s when pending/processing rows are visible
- [x] Design tokens — Terminal Intelligence aesthetic (Space Mono + Inter, amber on black)

**Lesson:** Category color tokens (`--cat-homelab`, `--cat-velvet`, etc.) belong in `:root` as CSS vars, not hardcoded per template.

---

## Phase 6 — Containerization ✅

- [x] `web/Dockerfile` — Python 3.12-slim + curl (for healthcheck)
- [x] `worker/Dockerfile` — Python 3.12-slim
- [x] `docker-compose.yml` — both services, healthcheck, log rotation, 256MB limits
- [x] `worker depends_on web condition: service_healthy`
- [x] Build context is repo root so `COPY shared/` works for both images
- [x] `prompt_context.md` bind-mounted read-only — host edits propagate without rebuild

**Lesson:** `curl` is not in `python:3.12-slim` — must be installed for Docker healthcheck.

---

## Phase 7 — Local deploy + dogfood ⬜

**Goal:** Get TubeIntel running on **Serverrig** (`192.168.1.5`, Windows 11 + Docker Desktop) behind the firewall, fully usable through Discord, without ever exposing it to the public internet. The Discord bot only needs **outbound** connectivity (Discord API + webhook), so you can submit URLs from `#yt-submit` on your phone from anywhere without the dashboard being public. The dashboard is reachable from the LAN at `http://192.168.1.5:5090` when you actually need to look at it.

**Host choice rationale:** Serverrig already runs Docker Compose + Portainer for PhotoPrism, Homepage, and the Minecraft stack — TubeIntel slots in next to them with no new VM/LXC to provision. The 256 MB footprint is rounding error on a 128 GB box. Trade-off accepted: SQLite lives on a Windows-backed Docker volume (fine at this write rate; see caveat below). A future migration to a dedicated Proxmox LXC, if ever needed, is `docker compose down` → `tar` the `./data` volume → `docker compose up` on the new host.

This phase exists so we get dogfooding signal before spending time on the auth + public-exposure work in Phase 8.

### Pre-flight code cleanup

Low-risk fixes worth doing **before** the first run so the long-running worker doesn't accumulate problems:

- [ ] Wrap DB connections in `contextlib.closing()` — fixes the connection leak in long-running worker
- [ ] Drop `yt-dlp>=2024.1` from `worker/requirements.txt` (unused; saves image size)
- [ ] Remove redundant `except (ValueError, Exception)` in `worker/pipeline.py:266`
- [ ] Add `add_done_callback` to fire-and-forget catch-up tasks in `scheduler.py:130`
- [ ] Set `app.config["MAX_CONTENT_LENGTH"] = 64 * 1024` in `create_app` (cheap defense-in-depth)

### Observability (small upfront investment)

Worth doing now so Phase 7 dogfooding produces actionable logs, not noise.

- [ ] Invoke `app-logging` skill → JSON-format logs, `video_id` correlation field through the pipeline
- [ ] Log a startup banner with which keys are present/missing (booleans only, no secret values)
- [ ] Confirm Docker log rotation works as configured (`docker logs --tail` is bounded)

### Deploy to Serverrig

- [ ] Clone repo to `c:/Code/tube-intel` on Serverrig (matches the existing convention used by PhotoPrism, Homepage, Minecraft stacks)
- [ ] Copy `.env.example` → `.env` and fill in real values:
  - `ANTHROPIC_API_KEY` (required)
  - `DISCORD_BOT_TOKEN` + `DISCORD_SUBMIT_CHANNEL_ID` (for inbound submissions)
  - `DISCORD_WEBHOOK_URL` (for outbound notifications)
- [ ] Edit `prompt_context.md` on Serverrig — verify current project status (TestMaker / MoneyFinder / FIBI / Velvet & Verve) is accurate before first run
- [ ] **Confirm port 5090 is NOT forwarded on the UDM Pro and NOT routed in NPM** — this phase is explicitly LAN-only
- [ ] Verify port 5090 is not already taken on Serverrig (`netstat -ano | findstr :5090` should be empty — game/photoprism/homepage ports are 25565+, 2342, 3005, 7575, 8085, none collide)
- [ ] `docker compose up -d --build` from `c:/Code/tube-intel`
- [ ] Verify both containers healthy: `docker compose ps`
- [ ] From a LAN device, hit `http://192.168.1.5:5090/health` → `{"ok": true}`
- [ ] (Optional) Add TubeIntel to Portainer's stack list for parity with the other services
- [ ] (Optional) Add a tile to Homepage (port 3005) linking to `http://192.168.1.5:5090`

### Windows + Docker Desktop caveats

- [ ] Confirm Docker Desktop's WSL2 integration is enabled (not Hyper-V backend) — matches every other Serverrig container's expected behavior
- [ ] Note: SQLite WAL on a Windows-backed bind mount works but is sensitive to `wsl --shutdown`. If the worker logs `database is locked` after a WSL restart, `docker compose restart` clears it. Not expected at TubeIntel's 1-write-per-30s rate.
- [ ] Confirm `docker compose` (not `docker-compose`) is the binary in use — the v2 plugin is what Portainer/the other stacks already use
- [ ] If Serverrig reboots (NiceHash GPU driver updates, monthly patches, etc.), the `restart: unless-stopped` policy will bring the containers back; APScheduler will catch up on startup

### Backup hygiene (Serverrig has no automated backups)

- [ ] Add a Windows Task Scheduler job: nightly at 03:00, `docker exec tube-intel-web-1 sqlite3 /data/tubeintel.db ".backup /data/tubeintel.$(Get-Date -Format yyyyMMdd).bak"` (or the bash-equivalent inside the container) — keeps a rolling on-disk backup of the SQLite DB
- [ ] Keep the last 7 backups; prune older (one-line PowerShell)
- [ ] (Defer) When Serverrig backups are properly configured at the homelab level, this folder gets included automatically — no separate plan needed

### Discord setup

- [ ] Confirm the Discord bot's `MESSAGE CONTENT` privileged intent is enabled in the developer portal
- [ ] Confirm `DISCORD_SUBMIT_CHANNEL_ID` is the numeric channel ID (Developer Mode → right-click → Copy Channel ID)
- [ ] Confirm the bot is invited to the server with `View Channel` + `Send Messages` + `Add Reactions` in `#yt-submit`
- [ ] Confirm the webhook for `#yt-intel` is created and the URL is in `.env`

### Smoke test (the real success criteria for Phase 7)

- [ ] **Manual submit:** open `http://192.168.1.5:5090/submit` from a LAN device, paste a YouTube URL with captions → confirm row appears, analysis completes within ~60s, Discord notification posts to `#yt-intel`
- [ ] **Discord submit:** post a YouTube URL in `#yt-submit` from your phone (off the home network) → bot reacts ✅, replies with the queued title, analysis completes, notification posts to `#yt-intel`. This is the primary success criterion — proves the off-LAN workflow without public exposure.
- [ ] **Channel polling:** add a known-active channel (one that posts frequently) → confirm RSS catch-up fires immediately at startup, new videos queue and process
- [ ] **Failure mode coverage:** submit a video with no captions → confirm `fail_reason="no_transcript"` row + Discord failure notification
- [ ] **Restart resilience:** `docker compose restart worker` → confirm pending jobs resume, scheduler catches up
- [ ] **Serverrig reboot resilience:** after the next scheduled Serverrig reboot (or simulate with `docker compose down && docker compose up -d`), confirm both containers come back healthy and scheduler catch-up fires

### Dogfood for at least one week

- [ ] Submit videos from `#yt-submit` from your phone during the workday
- [ ] Add 3–5 channels you actually watch to the watch list
- [ ] Keep a running list of what's broken / awkward / missing → feed into Phase 8 and Phase 9 scope
- [ ] Track Claude API spend per week — confirm it's sustainable before pushing toward public exposure

### Deliverables

- Update `CLAUDE.md` "Current Build State" to mark Phase 7 ✅
- Sync updated `CLAUDE.md` + `PLAN.md` to `c:/Code/referenceMDs-local-export/tube-intel/`
- Open and merge a `phase-7-serverrig-deploy` PR with the code cleanup + logging changes

---

## Phase 8 — Public exposure + hardening ⬜

**Goal:** Make TubeIntel safe to expose at `youtube-intel.bookclub44.com` so the dashboard is reachable from outside the LAN. Triggered only after Phase 7 has been running locally for at least a week and you actively need off-LAN dashboard access (the Discord flow does not require this).

Until every item in this phase is checked, do **not** route the public hostname.

### Pre-deploy blockers

- [ ] **Add auth gate to `/api/*`** — pick one:
  - Authentik forward-auth via NPM (preferred; reuses existing IdP at `192.168.1.217:9000`)
  - NPM IP allowlist (homelab LAN + Chris's phone IP) — simpler, no IdP dependency
  - Shared-secret header (`X-TubeIntel-Token`) — last-resort if NPM auth blocks the Discord bot's local API calls (only matters if the worker is moved off the LXC; same-host Docker network is already gated)
- [ ] **Add API-edge rate limit** — `Flask-Limiter`, 10/min/IP on `/api/submit`, 30/min on read endpoints
- [ ] Verify the worker → `web:5090` call still works through the auth layer (bot submissions must not break)

### Reverse proxy + DNS

- [ ] Add `youtube-intel.bookclub44.com` to Cloudflare DNS → UDM Pro
- [ ] Configure NPM proxy host on `nginx-vm` (192.168.1.106) → `192.168.1.5:5090` (Serverrig)
- [ ] Verify SSL cert provisions via NPM
- [ ] Verify `https://youtube-intel.bookclub44.com/health` returns `{"ok": true}` **from outside the LAN**
- [ ] Confirm auth gate is active **before announcing the URL** — test from an unauthenticated browser

### Smoke test from outside

- [ ] Confirm an unauthenticated request to `/api/submit` returns 401/403 (not a queued video)
- [ ] Confirm rate limit triggers at the expected threshold (try 11 rapid submits, expect 429 on the 11th)
- [ ] Confirm the dashboard loads from a coffee-shop network through the auth flow
- [ ] Confirm Discord bot submissions still work (they should — they never touch the public hostname)

### Deliverables

- README updated with the production URL once live
- Notes added to `referenceMDs-local-export/tube-intel/CLAUDE.md` for cross-system transfer
- Open a `phase-8-public-exposure` PR per the standard git workflow

---

## Phase 9 — Daily-use polish ⬜

Triggered after Phase 7 ships and you've been dogfooding for at least a week. Independent of Phase 8 — these are about making daily use nicer, not about public exposure.

- [ ] Re-analyze button on video detail (drop `analysis` row, set `status='pending'`)
- [ ] "Mark as viewed" — DB column + per-card unread indicator
- [ ] Pinned category color legend in nav
- [ ] Channels UI polish — truncate channel ID, fix yellow-on-yellow toggle contrast
- [ ] Wider desktop layout (880px) or 2-column variant at ≥1200px
- [ ] Daily Discord digest at 08:00 — top 3 per category from the last 24h
- [ ] Manual category override (DB column + dropdown on detail page)

---

## Phase 10 — Strategic decision ⬜

After Phase 9, decide: **internal tool or productize?**

- [ ] Confirm dogfooding signal — is TubeIntel saving more time than it costs in Claude credits?
- [ ] Decide brand direction (Refine / Evolve / Reimagine per `PROJECT_REVIEW.md`)
- [ ] If productizing: prototype the "Watchtower" daily-digest landing page as the main view
- [ ] If staying internal: invest in features that matter only to Chris (FTS search, knowledge graph, browser extension)

This phase is intentionally open — re-scope when triggered.

---

## Out of scope (not promised, not planned)

These come from the `PROJECT_REVIEW.md` enhancement list. Captured here so they don't get lost, but not committed to any phase:

- "Ask TubeIntel" semantic search with Claude + citations
- Browser extension for right-click submit
- Knowledge graph visualization
- Whisper local fallback for transcripts
- Bulk OPML channel import
- Apply-to-project deeplink generator

---

## Tracking conventions

- Each phase opens on its own short-lived branch (`phase-N-short-description`)
- All work merges via PR to `main` — no direct pushes
- Update this `PLAN.md` (check the boxes) as part of the same PR that delivers each item
- After a phase completes, sync the updated CLAUDE.md + PLAN.md to `c:/Code/referenceMDs-local-export/tube-intel/`
