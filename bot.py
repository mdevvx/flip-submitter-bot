import os
import discord
from discord.ext import commands
from logger import get_logger
from dotenv import load_dotenv

# load environment
load_dotenv()

logger = get_logger("bot")

intents = discord.Intents.all()
intents.guilds = True
intents.members = True

APP_ID = os.getenv("DISCORD_APP_ID")

bot = commands.Bot(command_prefix="!", intents=intents, application_id=APP_ID)


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (id: {bot.user.id})")
    # load cogs
    try:
        await bot.load_extension("cogs.flip")
        await bot.load_extension("cogs.admin")
        logger.info("Cogs loaded.")
    except Exception as e:
        logger.exception("Failed to load cogs: %s", e)
    # sync global commands (be careful in prod, may use guild sync for dev)
    try:
        await bot.tree.sync()
        logger.info("Command tree synced.")
    except Exception as e:
        logger.warning("Could not sync command tree: %s", e)


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN missing.")
        raise SystemExit("DISCORD_TOKEN missing.")
    bot.run(token)
