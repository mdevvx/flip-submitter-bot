import discord
from discord.ext import commands
from discord import app_commands
from db.supabase import (
    get_pending_flips,
    update_flip,
    add_user_profit,
    get_leaderboard_top,
    ensure_guild_settings,
    supabase,
)
from logger import get_logger
from utils.helpers import build_flip_embed, build_leaderboard_embed
from datetime import datetime

logger = get_logger("admin")


class ApproveView(discord.ui.View):
    def __init__(self, flip_row, cog):
        super().__init__(timeout=None)
        self.flip = flip_row
        self.cog = cog

    @discord.ui.button(
        label="Approve", style=discord.ButtonStyle.success, custom_id="approve_flip"
    )
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()
        try:
            update_flip(
                self.flip["id"],
                {
                    "status": "approved",
                    "handled_by": interaction.user.id,
                    "handled_at": "now()",
                },
            )

            user_obj = interaction.guild.get_member(self.flip["user_id"])
            username = user_obj.name if user_obj else str(self.flip["user_id"])

            add_user_profit(
                interaction.guild.id,
                self.flip["user_id"],
                username,
                float(self.flip.get("profit") or 0.0),
            )

            # send (or edit) the leaderboard summary (this reads from users table)
            try:
                await self.cog.send_leaderboard_summary(interaction.guild)
            except Exception:
                logger.exception("Failed to send leaderboard summary after approval")

            await interaction.message.edit(content="Flip approved ✅", view=None)
            await interaction.followup.send("Flip approved and posted.", ephemeral=True)
        except Exception as e:
            logger.exception("Error approving flip: %s", e)
            try:
                await interaction.message.edit(
                    content="Failed to approve (see logs).", view=None
                )
            except Exception:
                pass
            await interaction.followup.send(
                "Failed to approve flip. Check logs.", ephemeral=True
            )

    @discord.ui.button(
        label="Deny", style=discord.ButtonStyle.danger, custom_id="deny_flip"
    )
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            update_flip(
                self.flip["id"],
                {
                    "status": "denied",
                    "handled_by": interaction.user.id,
                    "handled_at": "now()",
                },
            )
            await interaction.message.edit(content="Flip denied ❌", view=None)
            await interaction.followup.send("Flip denied.", ephemeral=True)
        except Exception as e:
            logger.exception("Error denying flip: %s", e)
            try:
                await interaction.message.edit(
                    content="Failed to deny (see logs).", view=None
                )
            except Exception:
                pass
            await interaction.followup.send(
                "Failed to deny flip. Check logs.", ephemeral=True
            )


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def send_leaderboard_summary(self, guild: discord.Guild):
        """
        Send or update a single 'Leaderboard Summary' message in the leaderboard channel.
        Robust to the following:
        - stored summary message deleted (fetch -> 404) => send a new message
        - missing DB column for leaderboard_summary_message_id (we assume it's present after SQL)
        - uses authoritative users table via get_leaderboard_top
        """
        try:
            # 1) read authoritative users table via get_leaderboard_top
            rows = get_leaderboard_top(guild.id, limit=1000) or []

            # 2) compute total profit
            total = 0.0
            for r in rows:
                try:
                    total += float(r.get("total_profit") or 0.0)
                except Exception:
                    logger.debug("Skipping unparsable total_profit for row: %s", r)

            # 3) build embed
            embed = discord.Embed(
                title="🏆 Leaderboard Summary", description="Totals and participants"
            )
            try:
                embed.add_field(
                    name="Total profit", value=f"${total:,.2f}", inline=False
                )
            except Exception:
                embed.add_field(name="Total profit", value=str(total), inline=False)

            if rows:
                lines = []
                medals = ["🥇", "🥈", "🥉"]
                for i, r in enumerate(rows, start=1):
                    uid = r.get("id")
                    mention = f"<@{uid}>" if uid else "Unknown"
                    try:
                        profit = float(r.get("total_profit") or 0)
                    except Exception:
                        profit = 0.0
                    profit_str = f"${profit:,.2f}"
                    rank_icon = medals[i - 1] if i <= 3 else f"#{i}"
                    lines.append(f"{rank_icon} {mention} — {profit_str}")
                participants_text = "\n".join(lines)

            else:
                participants_text = "No participants yet."

            # Trim to 1024 char field limit
            if len(participants_text) > 1024:
                participants_text = participants_text[:1000] + "\n…"

            embed.add_field(name="Participants", value=participants_text, inline=False)

            # 4) find leaderboard channel & existing summary message id
            settings = ensure_guild_settings(guild.id)
            lb_chan_id = settings.get("leaderboard_channel_id")
            summary_msg_id = settings.get("leaderboard_summary_message_id")
            lb_channel = (
                guild.get_channel(lb_chan_id)
                if lb_chan_id
                else discord.utils.get(guild.text_channels, name="leaderboard")
            )

            if not lb_channel:
                logger.info(
                    "No leaderboard channel to send summary for guild %s", guild.id
                )
                return

            # 5) Try to edit existing summary message; if not found (404) send a new one
            sent_msg = None
            if summary_msg_id:
                try:
                    msg = await lb_channel.fetch_message(summary_msg_id)
                    await msg.edit(embed=embed)
                    sent_msg = msg
                except discord.NotFound:
                    logger.warning(
                        "Stored leaderboard summary message was not found (deleted). Will send a new one."
                    )
                except Exception as e:
                    logger.exception(
                        "Could not fetch/edit leaderboard summary message: %s", e
                    )

            # If we didn't successfully edit an existing message, send a new one
            if not sent_msg:
                try:
                    sent_msg = await lb_channel.send(embed=embed)
                except Exception as e:
                    logger.exception(
                        "Failed to send leaderboard summary message: %s", e
                    )
                    return

                # Upsert the new summary message id into guild_settings
                try:
                    supabase.table("guild_settings").upsert(
                        {
                            "guild_id": guild.id,
                            "leaderboard_summary_message_id": sent_msg.id,
                            "leaderboard_channel_id": lb_channel.id,
                        }
                    ).execute()
                except Exception as e:
                    # This should now work if you ran the SQL above; if it still fails, log with details.
                    logger.exception(
                        "Failed to upsert leaderboard_summary_message_id into guild_settings: %s",
                        e,
                    )

        except Exception as e:
            logger.exception("Failed to build/send leaderboard summary: %s", e)

    @app_commands.command(
        name="showconfig",
        description="Display current bot configuration for this guild (channels & message IDs).",
    )
    @app_commands.default_permissions(administrator=True)
    async def showconfig(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.followup.send(
                "You need Manage Server permission to use this.", ephemeral=True
            )
            return

        try:
            settings = ensure_guild_settings(interaction.guild.id) or {}

            # Channel IDs we store in guild_settings
            member_flips_chan_id = settings.get("member_flips_channel_id")
            leaderboard_chan_id = settings.get("leaderboard_channel_id")
            log_chan_id = settings.get("log_channel_id")

            # Stored message IDs (optional)
            lb_msg_id = settings.get("leaderboard_message_id")
            lb_summary_msg_id = settings.get("leaderboard_summary_message_id")

            # Resolve channel mentions if possible
            def chan_display(guild, cid):
                if not cid:
                    return "Not set"
                ch = guild.get_channel(cid)
                if ch:
                    return f"{ch.mention} (id: `{cid}`)"
                return f"ID: `{cid}` (channel not found)"

            embed = discord.Embed(
                title="🔧 Bot Configuration",
                description=f"Server: **{interaction.guild.name}** (`{interaction.guild.id}`)",
            )

            embed.add_field(
                name="Member flips channel",
                value=chan_display(interaction.guild, member_flips_chan_id),
                inline=False,
            )
            embed.add_field(
                name="Leaderboard channel",
                value=chan_display(interaction.guild, leaderboard_chan_id),
                inline=False,
            )
            embed.add_field(
                name="Log channel",
                value=chan_display(interaction.guild, log_chan_id),
                inline=False,
            )

            embed.add_field(
                name="Leaderboard summary message id",
                value=str(lb_summary_msg_id) if lb_summary_msg_id else "Not set",
                inline=True,
            )

            # Provide quick guidance
            embed.set_footer(
                text="Use /setchannels and /setlogchannel to update these settings."
            )

            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.exception("Failed to show config: %s", e)
            await interaction.followup.send(
                "Failed to fetch configuration. Check logs.", ephemeral=True
            )

    @app_commands.command(
        name="setchannels",
        description="Configure member-flips and leaderboard channels",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        member_flips_channel="Channel for approved flips",
        leaderboard_channel="Channel for leaderboard message",
    )
    async def setchannels(
        self,
        interaction: discord.Interaction,
        member_flips_channel: discord.TextChannel,
        leaderboard_channel: discord.TextChannel,
    ):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.followup.send(
                "You need Manage Server permission to use this.", ephemeral=True
            )
            return
        try:
            supabase.table("guild_settings").upsert(
                {
                    "guild_id": interaction.guild.id,
                    "member_flips_channel_id": member_flips_channel.id,
                    "leaderboard_channel_id": leaderboard_channel.id,
                }
            ).execute()
            await interaction.followup.send("✅ Channels configured.", ephemeral=True)
        except Exception as e:
            logger.exception("Failed to set channels: %s", e)
            await interaction.followup.send(
                "Failed to configure channels. Check logs.", ephemeral=True
            )

    @app_commands.command(
        name="setlogchannel",
        description="Configure a logging channel for flip events",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(log_channel="Channel where flip logs will be posted")
    async def setlogchannel(
        self,
        interaction: discord.Interaction,
        log_channel: discord.TextChannel,
    ):
        await interaction.response.defer(ephemeral=True)
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.followup.send(
                "You need Manage Server permission to use this.", ephemeral=True
            )
            return
        try:
            supabase.table("guild_settings").upsert(
                {"guild_id": interaction.guild.id, "log_channel_id": log_channel.id}
            ).execute()
            await interaction.followup.send(
                "✅ Log channel configured.", ephemeral=True
            )
        except Exception as e:
            logger.exception("Failed to set log channel: %s", e)
            await interaction.followup.send(
                "❌ Failed to configure log channel. Check logs.", ephemeral=True
            )

    @app_commands.command(name="pingdb", description="Check Supabase connectivity")
    @app_commands.default_permissions(administrator=True)
    async def pingdb(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        from db.supabase import ping

        ok, msg = ping()
        if ok:
            await interaction.followup.send(
                "✅ Supabase connected: " + str(msg), ephemeral=True
            )
        else:
            await interaction.followup.send(
                "❌ Supabase ping failed: " + str(msg), ephemeral=True
            )

    @app_commands.command(
        name="sync",
        description="Sync bot's slash commands globally (admin only).",
    )
    @app_commands.default_permissions(administrator=True)
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            synced = await self.bot.tree.sync()
            await interaction.followup.send(
                f"✅ Synced {len(synced)} global commands successfully.",
                ephemeral=True,
            )
            logger.info("Globally synced %s commands", len(synced))
        except Exception as e:
            logger.exception("Unexpected error during sync: %s", e)
            await interaction.followup.send(f"❌ Sync failed: {e}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
