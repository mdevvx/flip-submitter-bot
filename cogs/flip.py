import discord
from discord.ext import commands
from discord import app_commands
from logger import get_logger
from utils.helpers import build_flip_embed
from db.supabase import (
    ensure_guild_settings,
    insert_flip,
    update_flip,
    add_user_profit,
    get_leaderboard_top,
    supabase,
)

logger = get_logger("flip")


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


class ApproveRejectView(discord.ui.View):
    def __init__(self, flip_data: dict):
        super().__init__(timeout=None)
        self.flip = flip_data

    async def _is_moderator(self, interaction: discord.Interaction) -> bool:
        return (
            interaction.user.guild_permissions.manage_guild
            or interaction.user == interaction.guild.owner
        )

    async def _ensure_flip_row(self, guild_id: int):
        if self.flip.get("id"):
            return self.flip.get("id")

        # Fallback: attempt to find most recent pending flip by same submitter+item+profit
        try:
            qry = (
                supabase.table("flips")
                .select("*")
                .eq("guild_id", guild_id)
                .eq("user_id", self.flip.get("user_id"))
                .eq("item", self.flip.get("item"))
                .eq("status", "pending")
                .order("created_at", desc=True)
                .limit(1)
            )
            resp = qry.execute()
            rows = None
            try:
                rows = resp.data
            except Exception:
                try:
                    rows = resp.get("data")
                except Exception:
                    rows = None

            if rows:
                row = rows[0]
                self.flip["id"] = row.get("id")
                if row.get("member_message_id"):
                    self.flip["member_message_id"] = row.get("member_message_id")
                return self.flip["id"]
        except Exception:
            logger.exception("Fallback query to locate pending flip failed.")
        return None

    async def _edit_submission_message(
        self,
        guild: discord.Guild,
        member_message_id: int,
        actor_user_id: int,
        approved: bool,
    ):
        try:
            settings = ensure_guild_settings(guild.id)
            mf_chan_id = settings.get("member_flips_channel_id")
            member_channel = (
                guild.get_channel(mf_chan_id)
                if mf_chan_id
                else discord.utils.get(guild.text_channels, name="member-flips")
            )
            if not member_channel:
                logger.warning(
                    "Member flips channel not found to edit submission message."
                )
                return

            msg = await member_channel.fetch_message(member_message_id)
            header = (
                f"<@{actor_user_id}> — {'Approved ✅' if approved else 'Rejected ❌'}\n"
            )
            try:
                await msg.edit(
                    content=header,
                    embed=msg.embeds[0] if msg.embeds else None,
                    view=None,
                )
            except Exception:
                await member_channel.send(header)
        except Exception:
            logger.exception(
                "Failed to edit original submission message in member_flips channel."
            )

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not await self._is_moderator(interaction):
            return await interaction.response.send_message(
                "You don't have permission to approve flips.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)

        try:
            flip_id = await self._ensure_flip_row(interaction.guild.id)
            if not flip_id:
                logger.error(
                    "Attempted to approve flip but no DB id found: %s", self.flip
                )
                await interaction.followup.send(
                    "Failed to approve — could not locate the database row for this submission.",
                    ephemeral=True,
                )
                try:
                    await interaction.message.edit(
                        content="Failed to approve (no DB row).", view=None
                    )
                except Exception:
                    pass
                return

            update_flip(
                flip_id,
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

            member_message_id = self.flip.get("member_message_id")
            if not member_message_id:
                try:
                    resp = (
                        supabase.table("flips")
                        .select("member_message_id")
                        .eq("id", flip_id)
                        .single()
                        .execute()
                    )
                    row = (
                        getattr(resp, "data", None) or resp.get("data")
                        if isinstance(resp, dict)
                        else None
                    )
                    if row:
                        member_message_id = row.get("member_message_id")
                except Exception:
                    logger.debug(
                        "Could not fetch member_message_id from DB for flip id %s",
                        flip_id,
                    )

            if member_message_id:
                await self._edit_submission_message(
                    interaction.guild,
                    int(member_message_id),
                    self.flip["user_id"],
                    True,
                )
            else:
                try:
                    await interaction.message.edit(
                        content=f"<@{self.flip['user_id']}> — Approved ✅", view=None
                    )
                except Exception:
                    pass

            admin_cog = interaction.client.get_cog("AdminCog")
            if admin_cog:
                try:
                    await admin_cog.send_leaderboard_summary(interaction.guild)
                except Exception:
                    logger.exception("Failed sending leaderboard summary after approve")

            await send_log_message(
                interaction.guild,
                f"✅ **Flip approved:** {self.flip.get('item')} (submitted by <@{self.flip['user_id']}>)",
            )

            # await interaction.followup.send("Flip approved and saved.", ephemeral=True)

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

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._is_moderator(interaction):
            return await interaction.response.send_message(
                "You don't have permission to reject flips.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True)

        try:
            flip_id = await self._ensure_flip_row(interaction.guild.id)
            if flip_id:
                update_flip(
                    flip_id,
                    {
                        "status": "denied",
                        "handled_by": interaction.user.id,
                        "handled_at": "now()",
                    },
                )

            member_message_id = self.flip.get("member_message_id")
            if not member_message_id and flip_id:
                try:
                    resp = (
                        supabase.table("flips")
                        .select("member_message_id")
                        .eq("id", flip_id)
                        .single()
                        .execute()
                    )
                    row = (
                        getattr(resp, "data", None) or resp.get("data")
                        if isinstance(resp, dict)
                        else None
                    )
                    if row:
                        member_message_id = row.get("member_message_id")
                except Exception:
                    logger.debug(
                        "Could not fetch member_message_id for rejected flip id %s",
                        flip_id,
                    )

            if member_message_id:
                await self._edit_submission_message(
                    interaction.guild,
                    int(member_message_id),
                    self.flip["user_id"],
                    False,
                )
            else:
                try:
                    await interaction.message.edit(
                        content=f"<@{self.flip['user_id']}> — Rejected ❌", view=None
                    )
                except Exception:
                    pass

            await send_log_message(
                interaction.guild,
                f"❌ **Flip rejected:** {self.flip.get('item')} (submitted by <@{self.flip['user_id']}>)",
            )
            # await interaction.followup.send("Flip rejected.", ephemeral=True)

        except Exception as e:
            logger.exception("Error rejecting flip: %s", e)
            try:
                await interaction.message.edit(
                    content="Failed to reject (see logs).", view=None
                )
            except Exception:
                pass
            await interaction.followup.send(
                "Failed to reject flip. Check logs.", ephemeral=True
            )


# ---- Single-step modal (all fields together) ----
class FlipModal(discord.ui.Modal, title="Submit a flip"):
    item = discord.ui.TextInput(
        label="Item",
        placeholder="e.g. 1997 Mercury 9.9",
        max_length=200,
        style=discord.TextStyle.short,
    )
    purchase_price = discord.ui.TextInput(
        label="Purchase price", style=discord.TextStyle.short, placeholder="0.00"
    )
    parts_price = discord.ui.TextInput(
        label="Parts price", style=discord.TextStyle.short, placeholder="0.00"
    )
    sales_price = discord.ui.TextInput(
        label="Sales price", style=discord.TextStyle.short, placeholder="0.00"
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Defer as ephemeral to avoid Discord timing out for slow DB/network
        await interaction.response.defer(ephemeral=True)

        try:
            pp = float(self.purchase_price.value.strip() or 0.0)
        except Exception:
            pp = 0.0
        try:
            parts = float(self.parts_price.value.strip() or 0.0)
        except Exception:
            parts = 0.0
        try:
            sp = float(self.sales_price.value.strip() or 0.0)
        except Exception:
            sp = 0.0

        total_cost = pp + parts
        profit = sp - total_cost

        flip_payload = {
            "guild_id": interaction.guild.id,
            "user_id": interaction.user.id,
            "item": self.item.value.strip(),
            "purchase_price": pp,
            "parts_price": parts,
            "sales_price": sp,
            "total_cost": total_cost,
            "profit": profit,
            "status": "pending",
        }

        try:
            inserted = insert_flip(flip_payload)
            inserted_id = None

            if isinstance(inserted, dict) and inserted.get("id"):
                inserted_id = inserted.get("id")
                flip_payload["id"] = inserted_id
            else:
                try:
                    possible = getattr(inserted, "data", None) or (
                        inserted.get("data") if isinstance(inserted, dict) else None
                    )
                    if possible:
                        row = (
                            possible[0]
                            if isinstance(possible, list) and possible
                            else possible
                        )
                        if isinstance(row, dict) and row.get("id"):
                            inserted_id = row.get("id")
                            flip_payload["id"] = inserted_id
                except Exception:
                    pass

            settings = ensure_guild_settings(interaction.guild.id)
            mf_chan_id = settings.get("member_flips_channel_id")
            member_channel = (
                interaction.guild.get_channel(mf_chan_id)
                if mf_chan_id
                else discord.utils.get(
                    interaction.guild.text_channels, name="member-flips"
                )
            )

            embed = build_flip_embed(
                flip_payload, author_name=f"<@{flip_payload['user_id']}>"
            )
            view = ApproveRejectView(flip_payload)

            if member_channel:
                posted = await member_channel.send(embed=embed, view=view)
            else:
                posted = await interaction.channel.send(embed=embed, view=view)

            try:
                member_message_id = posted.id
                flip_payload["member_message_id"] = member_message_id

                if inserted_id:
                    update_flip(inserted_id, {"member_message_id": member_message_id})
                else:
                    try:
                        resp = (
                            supabase.table("flips")
                            .select("id")
                            .eq("guild_id", interaction.guild.id)
                            .eq("user_id", interaction.user.id)
                            .eq("item", flip_payload.get("item"))
                            .eq("status", "pending")
                            .order("created_at", desc=True)
                            .limit(1)
                            .execute()
                        )
                        rows = getattr(resp, "data", None) or (
                            resp.get("data") if isinstance(resp, dict) else None
                        )
                        if rows:
                            candidate = rows[0]
                            cid = candidate.get("id")
                            if cid:
                                flip_payload["id"] = cid
                                update_flip(
                                    cid, {"member_message_id": member_message_id}
                                )
                    except Exception:
                        logger.exception(
                            "Fallback: could not locate pending flip row to attach member_message_id"
                        )
            except Exception:
                logger.exception(
                    "Failed to persist member_message_id for submitted flip"
                )

            # await interaction.followup.send(
            #     "Flip saved and posted to member-flips for admin approval.",
            #     ephemeral=True,
            # )
            if member_channel:
                channel_mention = member_channel.mention
            else:
                channel_mention = "#member-flips"

            await interaction.followup.send(
                f"✅ Flip saved and posted to {channel_mention} for admin approval.",
                ephemeral=True,
            )

            await send_log_message(
                interaction.guild,
                f"📝 **Flip submitted for approval by** {interaction.user.mention} — `{flip_payload['item']}` (Profit: ${profit:,.2f})",
            )

        except Exception as e:
            logger.exception("Error posting flip for approval: %s", e)
            await interaction.followup.send(
                "Failed to submit flip — please try again later.", ephemeral=True
            )


class FlipCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="flip", description="Submit a flip for approval (single modal)"
    )
    async def flip(self, interaction: discord.Interaction):
        from db.supabase import ensure_guild_settings

        settings = ensure_guild_settings(interaction.guild.id)
        member_flips_channel_id = settings.get("member_flips_channel_id")
        leaderboard_channel_id = settings.get("leaderboard_channel_id")

        if not member_flips_channel_id or not leaderboard_channel_id:
            await interaction.response.send_message(
                "**⚠️ Setup incomplete:**\n"
                "This command can't be used until both channels are configured.\n"
                "Please ask an admin to run `/setchannels` first.",
                ephemeral=True,
            )
            return

        member_flips_channel = interaction.guild.get_channel(member_flips_channel_id)
        leaderboard_channel = interaction.guild.get_channel(leaderboard_channel_id)

        if not member_flips_channel or not leaderboard_channel:
            await interaction.response.send_message(
                "**⚠️ Channel not found:** One or both configured channels no longer exist.\n"
                "Please have an admin re-run `/setchannels` to fix it.",
                ephemeral=True,
            )
            return

        modal = FlipModal()
        await interaction.response.send_modal(modal)


async def setup(bot):
    await bot.add_cog(FlipCog(bot))
