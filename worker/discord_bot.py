import logging
import os
import re
import sys

import discord
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

logger = logging.getLogger(__name__)

WEB_BASE = os.environ.get("WEB_BASE_URL", "http://web:5090")
_submit_channel_raw = os.environ.get("DISCORD_SUBMIT_CHANNEL_ID", "0").strip()
# Defaults to 0 (not an error) — on_message ignores all channels when SUBMIT_CHANNEL_ID is 0 (disabled gracefully)
SUBMIT_CHANNEL_ID = int(_submit_channel_raw) if _submit_channel_raw else 0

# Matches standard YouTube watch URLs and short youtu.be URLs
YT_URL_RE = re.compile(
    r'https?://(?:www\.)?(?:youtube\.com/watch\?[^\s]*v=|youtu\.be/)[a-zA-Z0-9_-]{11}'
)

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)


@bot.event
async def on_ready():
    logger.info(f"Discord bot ready as {bot.user}")


@bot.event
async def on_message(message: discord.Message):
    # Ignore bot messages and messages outside the submit channel
    if message.author.bot:
        return
    if message.channel.id != SUBMIT_CHANNEL_ID:
        return

    match = YT_URL_RE.search(message.content)
    if not match:
        return

    url = match.group(0)
    await message.add_reaction("✅")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{WEB_BASE}/api/submit",
                json={"url": url, "source": "discord"}
            )
            data = r.json()

        if data.get("status") == "exists":
            await message.reply("Already scanned — check the dashboard for results.")
        elif data.get("status") == "queued":
            title = None
            try:
                # Title isn't known at submit time — pipeline fetches it later; oEmbed gives an immediate label for the reply
                oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
                async with httpx.AsyncClient(timeout=5) as oe_client:
                    oe = await oe_client.get(oembed_url)
                    if oe.status_code == 200:
                        title = oe.json().get("title")
            except Exception:
                pass
            label = title if title else url
            await message.reply(f"Queued: {label} — I'll post results in #yt-intel when done.")
        else:
            await message.reply(f"Could not queue: {data.get('error', 'unknown error')}")
    except Exception as e:
        logger.error(f"Discord bot submit error: {e}")
        await message.reply("Failed to queue — check worker logs.")
