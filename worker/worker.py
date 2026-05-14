import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from shared.logging_config import setup_logging, get_logger, log_keys_present

setup_logging(service_name="tube-intel-worker")
logger = get_logger(__name__)

from worker.discord_bot import bot as discord_bot
from worker.scheduler import build_scheduler, reschedule_channels

DB_PATH = os.environ.get("DB_PATH", "/data/tubeintel.db")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")


async def main():
    from shared.db import init_db
    init_db(DB_PATH)

    logger.info(
        "worker_startup",
        db_path=DB_PATH,
        **log_keys_present(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            discord_bot_token=DISCORD_BOT_TOKEN,
            discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL", ""),
            discord_submit_channel_id=os.environ.get("DISCORD_SUBMIT_CHANNEL_ID", ""),
        ),
    )

    scheduler = build_scheduler(DB_PATH)
    scheduler.start()
    logger.info("scheduler_started")

    # Startup catch-up: immediately check all enabled channels (don't wait for first interval)
    await reschedule_channels(scheduler, DB_PATH)

    if DISCORD_BOT_TOKEN:
        logger.info("discord_bot_starting")
        # discord_bot.start() blocks the event loop forever — APScheduler still fires
        # because it posts coroutines to the same loop
        await discord_bot.start(DISCORD_BOT_TOKEN)
    else:
        logger.warning("discord_bot_disabled", reason="DISCORD_BOT_TOKEN not set")
        # Keep the event loop alive so APScheduler keeps firing
        while True:
            await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
