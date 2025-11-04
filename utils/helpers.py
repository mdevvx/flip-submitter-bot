import discord
from discord import Embed
from logger import get_logger
from db.supabase import (
    ensure_guild_settings,
)

logger = get_logger("flip")


def build_flip_embed(flip_row: dict, author_name: str):
    embed = Embed(
        title=str(flip_row.get("item", "Flip")),
        description=f"Submitted by {author_name}",
    )
    embed.add_field(
        name="Purchase price",
        value=str(flip_row.get("purchase_price", "")),
        inline=True,
    )
    embed.add_field(
        name="Parts price", value=str(flip_row.get("parts_price", "")), inline=True
    )
    embed.add_field(
        name="Total cost", value=str(flip_row.get("total_cost", "")), inline=True
    )
    embed.add_field(
        name="Sale price", value=str(flip_row.get("sales_price", "")), inline=True
    )
    embed.add_field(name="Profit", value=str(flip_row.get("profit", "")), inline=True)
    if flip_row.get("notes"):
        embed.add_field(name="Notes", value=flip_row.get("notes"), inline=False)
    if flip_row.get("photo_url"):
        embed.set_image(url=flip_row.get("photo_url"))
    return embed


def build_leaderboard_embed(rows):
    """Builds an embed showing top users on the leaderboard."""
    import discord

    embed = discord.Embed(
        title="🏆 Leaderboard",
        description="Top members by total profit",
    )

    if not rows:
        embed.add_field(name="No data", value="No approved flips yet.", inline=False)
        return embed

    medal_emojis = ["🥇", "🥈", "🥉"]

    lines = []
    for i, row in enumerate(rows, start=1):
        user_id = row.get("id")
        user_mention = f"<@{user_id}>" if user_id else "Unknown User"

        try:
            total_profit = float(row.get("total_profit") or 0)
        except Exception:
            total_profit = 0.0

        profit_str = f"${total_profit:,.2f}"

        if i <= 3:
            rank_display = medal_emojis[i - 1]
        else:
            rank_display = f"#{i}"

        lines.append(f"{rank_display} {user_mention} — {profit_str}")

    leaderboard_text = "\n".join(lines)
    embed.add_field(
        name="Leaderboard", value=leaderboard_text or "No data", inline=False
    )

    return embed


async def send_log_message(guild: discord.Guild, message: str):
    """Send a message to the configured log channel if available."""
    try:
        settings = ensure_guild_settings(guild.id)
        log_chan_id = settings.get("log_channel_id")
        if not log_chan_id:
            return  # no log channel set
        log_channel = guild.get_channel(log_chan_id)
        if not log_channel:
            return
        await log_channel.send(message)
    except Exception as e:
        logger.warning(f"Failed to send log message: {e}")


def clean_number(value: str) -> float:
    """Remove $ and commas safely before converting to float."""
    try:
        return float(value.replace("$", "").replace(",", "").strip() or 0.0)
    except Exception:
        return 0.0
