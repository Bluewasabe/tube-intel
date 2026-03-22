import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from worker.discord_bot import bot as discord_bot
from worker.scheduler import build_scheduler, reschedule_channels

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "/data/tubeintel.db")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")


async def main():
    from shared.db import init_db
    init_db(DB_PATH)
    logger.info(f"DB initialized at {DB_PATH}")

    scheduler = build_scheduler(DB_PATH)
    scheduler.start()
    logger.info("Scheduler started")

    # Startup catch-up: immediately check all enabled channels (don't wait for first interval)
    await reschedule_channels(scheduler, DB_PATH)

    if DISCORD_BOT_TOKEN:
        logger.info("Starting Discord bot")
        await discord_bot.start(DISCORD_BOT_TOKEN)
    else:
        logger.warning("DISCORD_BOT_TOKEN not set — Discord bot disabled, worker running scheduler only")
        # Keep the event loop alive so APScheduler keeps firing
        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
