import asyncio
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.client.commands import has_permissions
from core.client.embed import Embed
from core.config import COLORS

MAX_HUBS_PER_GUILD = 3
BITRATE_RANGE = range(8, 97)
VOICE_REGIONS = {
    "auto", "us-west", "us-east", "us-south", "us-central", "europe",
    "singapore", "sydney", "brazil", "hongkong", "russia", "japan",
    "southafrica", "india", "dubai",
}


class VoiceMasterButton(discord.ui.Button):
    """A button in the persistent VoiceMaster control panel."""

    def __init__(self, cog: "Voicemaster", action: str, label: str, style: discord.ButtonStyle, row: int, emoji: str):
        super().__init__(
            style=style,
            label=label,
            custom_id=f"vm:{action}",
            row=row,
            emoji=emoji,
        )
        self.cog = cog
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.action in ("lock", "unlock", "hide", "reveal", "temporary", "delete"):
            await self.cog.handle_simple_action(self.action, interaction)
        else:
            await self.cog.handle_modal_action(self.action, interaction)


class VoiceMasterView(discord.ui.View):
    """The interface attached to VoiceMaster channel messages."""

    def __init__(self, cog: "Voicemaster") -> None:
        super().__init__(timeout=None)
        self.cog = cog

        self.add_item(VoiceMasterButton(cog, "lock", "Lock", discord.ButtonStyle.danger, 0, "\U0001f512"))
        self.add_item(VoiceMasterButton(cog, "unlock", "Unlock", discord.ButtonStyle.success, 0, "\U0001f513"))
        self.add_item(VoiceMasterButton(cog, "hide", "Hide", discord.ButtonStyle.secondary, 0, "\U0001f47b"))
        self.add_item(VoiceMasterButton(cog, "reveal", "Reveal", discord.ButtonStyle.secondary, 0, "\U0001f440"))
        self.add_item(VoiceMasterButton(cog, "temporary", "Auto-Delete", discord.ButtonStyle.secondary, 0, "\u23f1\ufe0f"))

        self.add_item(VoiceMasterButton(cog, "limit", "Limit", discord.ButtonStyle.secondary, 1, "\U0001f465"))
        self.add_item(VoiceMasterButton(cog, "bitrate", "Bitrate", discord.ButtonStyle.secondary, 1, "\U0001f4c8"))
        self.add_item(VoiceMasterButton(cog, "claim", "Claim", discord.ButtonStyle.primary, 1, "\U0001f3b2"))
        self.add_item(VoiceMasterButton(cog, "drag", "Drag", discord.ButtonStyle.secondary, 1, "\U0001f9ed"))

        self.add_item(VoiceMasterButton(cog, "permit", "Permit", discord.ButtonStyle.secondary, 2, "\u2705"))
        self.add_item(VoiceMasterButton(cog, "reject", "Reject", discord.ButtonStyle.secondary, 2, "\u26d4"))
        self.add_item(VoiceMasterButton(cog, "region", "Region", discord.ButtonStyle.secondary, 2, "\U0001f30d"))

        self.add_item(VoiceMasterButton(cog, "delete", "Delete", discord.ButtonStyle.danger, 3, "\U0001f5d1\ufe0f"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        user = interaction.user
        if not isinstance(user, discord.Member) or not user.voice or not isinstance(user.voice.channel, discord.VoiceChannel):
            await interaction.response.send_message(
                embed=Embed(description=f"{user.mention}: you need to be in a **voice channel** to use these buttons.", color=COLORS.deny),
                ephemeral=True,
            )
            return False

        code = interaction.data.get("custom_id", "").rsplit(":", 1)[-1]
        channel = user.voice.channel
        temp = self.cog._temp_channels.get(channel.id)

        if code == "claim":
            if temp is None:
                await interaction.response.send_message(
                    embed=Embed(description=f"{user.mention}: this isn't a **temporary** voice channel.", color=COLORS.deny),
                    ephemeral=True,
                )
                return False
            return True

        if temp is None or temp["owner_id"] != user.id:
            await interaction.response.send_message(
                embed=Embed(description=f"{user.mention}: you're not the **owner** of this temporary voice channel.", color=COLORS.deny),
                ephemeral=True,
            )
            return False
        return True


class VoiceMasterModal(discord.ui.Modal):
    """Modal used for the claim / limit / bitrate / drag / permit / reject buttons."""

    def __init__(self, cog: "Voicemaster", action: str, title: str, label: str, placeholder: str) -> None:
        super().__init__(title=title, timeout=120)
        self.cog = cog
        self.action = action
        self.input = discord.ui.TextInput(
            label=label,
            placeholder=placeholder,
            style=discord.TextStyle.short,
            required=True,
            max_length=64,
            custom_id=f"vm:{action}:value",
        )
        self.add_item(self.input)

    def _resolve_member(self, interaction: discord.Interaction, value: str) -> Optional[discord.Member]:
        value = value.strip()
        if value.isdigit():
            return interaction.guild.get_member(int(value))
        if value.startswith("<@") and value.endswith(">"):
            value = value.strip("<@!>")
            if value.isdigit():
                return interaction.guild.get_member(int(value))
        return discord.utils.find(
            lambda m: m.name == value or (m.nick == value),
            interaction.guild.members,
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        user = interaction.user
        if not isinstance(user, discord.Member) or not user.voice or not isinstance(user.voice.channel, discord.VoiceChannel):
            return await interaction.response.send_message(
                embed=Embed(description=f"{user.mention}: join a **temporary** voice channel first.", color=COLORS.deny),
                ephemeral=True,
            )
        channel = user.voice.channel
        value = self.input.value

        if self.action == "claim":
            if value.strip().lower() not in ("yes", "y", "true", "1"):
                return await interaction.response.send_message(
                    embed=Embed(description=f"{user.mention}: type `yes` to claim **{channel.mention}**.", color=COLORS.deny),
                    ephemeral=True,
                )
            if not await self.cog.set_temp_owner(channel.id, user.id):
                return await interaction.response.send_message(
                    embed=Embed(description=f"{user.mention}: this isn't a **temporary** voice channel.", color=COLORS.deny),
                    ephemeral=True,
                )
            return await interaction.response.send_message(
                embed=Embed(description=f"{user.mention}: you're now the **owner** of {channel.mention}.", color=COLORS.approve),
                ephemeral=True,
            )

        if self.action in ("limit", "bitrate"):
            try:
                number = int(value)
            except ValueError:
                return await interaction.response.send_message(
                    embed=Embed(description=f"{user.mention}: **{value}** isn't a valid number.", color=COLORS.deny),
                    ephemeral=True,
                )
            if self.action == "limit":
                if not 0 <= number <= 99:
                    return await interaction.response.send_message(
                        embed=Embed(description=f"{user.mention}: limit must be between **0** and **99**.", color=COLORS.deny),
                        ephemeral=True,
                    )
                try:
                    await channel.edit(user_limit=number)
                except (discord.Forbidden, discord.HTTPException) as error:
                    return await interaction.response.send_message(
                        embed=Embed(description=f"{user.mention}: failed to set the limit: `{error}`", color=COLORS.deny),
                        ephemeral=True,
                    )
                return await interaction.response.send_message(
                    embed=Embed(description=f"{user.mention}: set **{channel.mention}**'s user limit to **{number or 'unlimited'}**.", color=COLORS.neutral),
                    ephemeral=True,
                )
            if number not in BITRATE_RANGE:
                return await interaction.response.send_message(
                    embed=Embed(description=f"{user.mention}: bitrate must be between **8** and **96**.", color=COLORS.deny),
                    ephemeral=True,
                )
            try:
                await channel.edit(bitrate=number * 1000)
            except (discord.Forbidden, discord.HTTPException) as error:
                return await interaction.response.send_message(
                    embed=Embed(description=f"{user.mention}: failed to set the bitrate: `{error}`", color=COLORS.deny),
                    ephemeral=True,
                )
            return await interaction.response.send_message(
                embed=Embed(description=f"{user.mention}: set **{channel.mention}**'s bitrate to **{number} kbps**.", color=COLORS.neutral),
                ephemeral=True,
            )

        if self.action in ("drag", "permit", "reject"):
            member = self._resolve_member(interaction, value)
            if member is None:
                return await interaction.response.send_message(
                    embed=Embed(description=f"{user.mention}: couldn't find **{value}** in this server.", color=COLORS.deny),
                    ephemeral=True,
                )
            if self.action == "drag":
                if not member.voice:
                    return await interaction.response.send_message(
                        embed=Embed(description=f"{user.mention}: {member.mention} isn't in a **voice channel**.", color=COLORS.deny),
                        ephemeral=True,
                    )
                try:
                    await member.move_to(channel)
                except (discord.Forbidden, discord.HTTPException) as error:
                    return await interaction.response.send_message(
                        embed=Embed(description=f"{user.mention}: failed to drag {member.mention}: `{error}`", color=COLORS.deny),
                        ephemeral=True,
                    )
                return await interaction.response.send_message(
                    embed=Embed(description=f"{user.mention}: dragged {member.mention} into {channel.mention}.", color=COLORS.neutral),
                    ephemeral=True,
                )
            if self.action == "permit":
                try:
                    await channel.set_permissions(member, connect=True, view_channel=True)
                except (discord.Forbidden, discord.HTTPException) as error:
                    return await interaction.response.send_message(
                        embed=Embed(description=f"{user.mention}: failed to permit {member.mention}: `{error}`", color=COLORS.deny),
                        ephemeral=True,
                    )
                return await interaction.response.send_message(
                    embed=Embed(description=f"{user.mention}: allowed {member.mention} into {channel.mention}.", color=COLORS.neutral),
                    ephemeral=True,
                )
            if member.voice and member.voice.channel == channel:
                try:
                    await member.move_to(None)
                except (discord.Forbidden, discord.HTTPException):
                    pass
            try:
                await channel.set_permissions(member, view_channel=False, connect=False)
            except (discord.Forbidden, discord.HTTPException) as error:
                return await interaction.response.send_message(
                    embed=Embed(description=f"{user.mention}: failed to reject {member.mention}: `{error}`", color=COLORS.deny),
                    ephemeral=True,
                )
            return await interaction.response.send_message(
                embed=Embed(description=f"{user.mention}: removed and blocked {member.mention} from {channel.mention}.", color=COLORS.neutral),
                ephemeral=True,
            )


class TemporaryToggle(discord.ui.View):
    def __init__(self, cog: "Voicemaster", channel_id: int) -> None:
        super().__init__(timeout=120)
        self.cog = cog
        self.channel_id = channel_id

    @discord.ui.button(label="Enable", style=discord.ButtonStyle.success, custom_id="vm:temp:enable")
    async def enable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.set_temp(self.channel_id, temporary=1)
        await self._finish(interaction, "enabled")

    @discord.ui.button(label="Disable", style=discord.ButtonStyle.danger, custom_id="vm:temp:disable")
    async def disable(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.set_temp(self.channel_id, temporary=0)
        await self._finish(interaction, "disabled")

    async def _finish(self, interaction: discord.Interaction, state: str) -> None:
        await interaction.response.edit_message(
            embed=Embed(description=f"{interaction.user.mention}: automatic deletion **{state}**.", color=COLORS.neutral),
            view=None,
        )


class RegionSelect(discord.ui.Select):
    def __init__(self, cog: "Voicemaster") -> None:
        super().__init__(
            placeholder="Choose a region...",
            options=[discord.SelectOption(label=region.title(), value=region) for region in sorted(VOICE_REGIONS)],
            custom_id="vm:region:select",
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction) -> None:
        user = interaction.user
        if not isinstance(user, discord.Member) or not user.voice or not isinstance(user.voice.channel, discord.VoiceChannel):
            return await interaction.response.edit_message(
                embed=Embed(description=f"{user.mention}: join a **temporary** voice channel first.", color=COLORS.deny),
                view=None,
            )
        channel = user.voice.channel
        region = self.values[0]
        try:
            await channel.edit(rtc_region=region if region != "auto" else None)
        except (discord.Forbidden, discord.HTTPException) as error:
            return await interaction.response.edit_message(
                embed=Embed(description=f"{user.mention}: failed to set the region: `{error}`", color=COLORS.deny),
                view=None,
            )
        await interaction.response.edit_message(
            embed=Embed(description=f"{user.mention}: set **{channel.mention}**'s region to **{region}**.", color=COLORS.neutral),
            view=None,
        )


class Voicemaster(commands.Cog):
    """VoiceMaster: join-to-create temporary voice channels."""

    def __init__(self, bot) -> None:
        self.bot = bot
        self._guilds: dict[int, dict] = {}
        self._temp_channels: dict[int, dict] = {}

    # ------------------------------------------------------------------ #
    # Lifecycle & cache
    # ------------------------------------------------------------------ #
    async def cog_load(self) -> None:
        await self.load_hubs()
        self.bot.add_view(VoiceMasterView(self))

    async def load_hubs(self) -> None:
        try:
            cursor = await self.bot.db.execute(
                "SELECT guild_id, name, category_id, channel_id FROM vm_hub"
            )
            rows = await cursor.fetchall()
            for row in rows:
                self._guilds.setdefault(row["guild_id"], self._default_guild_config())["hubs"].append(row["channel_id"])
        except Exception:
            pass

        try:
            cursor = await self.bot.db.execute(
                "SELECT channel_id, guild_id, hub_id, owner_id, name, locked, hidden, temporary FROM vm_temp"
            )
            rows = await cursor.fetchall()
            for row in rows:
                self._temp_channels[row["channel_id"]] = {
                    "guild_id": row["guild_id"],
                    "hub_id": row["hub_id"],
                    "owner_id": row["owner_id"],
                    "name": row["name"],
                    "locked": row["locked"],
                    "hidden": row["hidden"],
                    "temporary": row["temporary"],
                }
        except Exception:
            pass

        for guild_id in list(self._guilds):
            await self._load_guild(guild_id)

    @staticmethod
    def _default_guild_config() -> dict:
        return {
            "hubs": [],
            "default_bitrate": 64,
            "default_region": "us-west",
            "default_role_id": None,
            "default_interface": False,
        }

    async def _load_guild(self, guild_id: int) -> None:
        try:
            cursor = await self.bot.db.execute(
                "SELECT default_bitrate, default_region, default_role_id, default_interface FROM vm_guild WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cursor.fetchone()
            if row:
                self._guilds[guild_id].update({
                    "default_bitrate": row["default_bitrate"],
                    "default_region": row["default_region"],
                    "default_role_id": row["default_role_id"],
                    "default_interface": bool(row["default_interface"]),
                })
        except Exception:
            pass

    def guild_config(self, guild_id: int) -> dict:
        if guild_id not in self._guilds:
            self._guilds[guild_id] = self._default_guild_config()
        return self._guilds[guild_id]

    # ------------------------------------------------------------------ #
    # Database helpers
    # ------------------------------------------------------------------ #
    async def _fetch(self, query: str, *args) -> Optional[dict]:
        cursor = await self.bot.db.execute(query, args)
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_hub(self, channel_id: int, guild_id: int) -> Optional[dict]:
        return await self._fetch(
            "SELECT id, name, category_id, channel_id FROM vm_hub WHERE channel_id = ? AND guild_id = ?",
            channel_id,
            guild_id,
        )

    async def is_hub_channel(self, channel: Optional[discord.VoiceChannel]) -> bool:
        if not isinstance(channel, discord.VoiceChannel):
            return False
        return channel.id in self.guild_config(channel.guild.id)["hubs"]

    async def get_defaults(self, guild_id: int) -> dict:
        config = self.guild_config(guild_id)
        return {
            "bitrate": config["default_bitrate"],
            "region": config["default_region"],
            "role_id": config["default_role_id"],
            "interface": config["default_interface"],
        }

    def owner_of(self, channel_id: int) -> Optional[int]:
        return self._temp_channels.get(channel_id, {}).get("owner_id")

    async def set_temp(self, channel_id: int, **fields) -> bool:
        if not fields or channel_id not in self._temp_channels:
            return False
        updates = ", ".join(f"{key} = ?" for key in fields)
        await self.bot.db.execute(
            f"UPDATE vm_temp SET {updates} WHERE channel_id = ?",
            (*fields.values(), channel_id),
        )
        await self.bot.db.commit()
        self._temp_channels[channel_id].update(fields)
        return True

    async def set_temp_owner(self, channel_id: int, owner_id: int) -> bool:
        return await self.set_temp(channel_id, owner_id=owner_id)

    async def remove_temp(self, channel_id: int) -> None:
        await self.bot.db.execute("DELETE FROM vm_temp WHERE channel_id = ?", (channel_id,))
        await self.bot.db.commit()
        self._temp_channels.pop(channel_id, None)

    async def add_temp(self, channel: discord.VoiceChannel, guild_id: int, hub_id: Optional[int], owner_id: int, name: str) -> None:
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO vm_temp (channel_id, guild_id, hub_id, owner_id, name) VALUES (?, ?, ?, ?, ?)",
            (channel.id, guild_id, hub_id, owner_id, name),
        )
        await self.bot.db.commit()
        self._temp_channels[channel.id] = {
            "guild_id": guild_id,
            "hub_id": hub_id,
            "owner_id": owner_id,
            "name": name,
            "locked": 0,
            "hidden": 0,
            "temporary": 1,
        }

    async def _save_guild(self, guild_id: int) -> None:
        config = self.guild_config(guild_id)
        await self.bot.db.execute(
            """
            INSERT INTO vm_guild (guild_id, default_bitrate, default_region, default_role_id, default_interface)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                default_bitrate = excluded.default_bitrate,
                default_region = excluded.default_region,
                default_role_id = excluded.default_role_id,
                default_interface = excluded.default_interface
            """,
            (
                guild_id,
                config["default_bitrate"],
                config["default_region"],
                config["default_role_id"],
                int(config["default_interface"]),
            ),
        )
        await self.bot.db.commit()

    # ------------------------------------------------------------------ #
    # Channel creation
    # ------------------------------------------------------------------ #
    async def create_temp(self, hub: dict, member: discord.Member) -> Optional[discord.VoiceChannel]:
        guild = member.guild
        category = guild.get_channel(hub["category_id"])
        if not isinstance(category, discord.CategoryChannel):
            return None

        defaults = await self.get_defaults(guild.id)
        bitrate = min(defaults["bitrate"] * 1000, 96_000)
        role_id = defaults["role_id"]
        role = guild.get_role(role_id) if role_id else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(
                view_channel=True,
                connect=(role is None),
            ),
        }
        if role and not role.is_default():
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, connect=True)

        display_name = member.display_name or member.name
        name = f"{display_name}'s channel"[:64]
        try:
            channel = await guild.create_voice_channel(
                name,
                category=category,
                overwrites=overwrites,
                bitrate=bitrate,
                rtc_region=defaults["region"] if defaults["region"] != "auto" else None,
                user_limit=0,
                reason=f"VoiceMaster: temp channel created for {member}",
            )
        except (discord.Forbidden, discord.HTTPException):
            return None

        await self.add_temp(channel, guild.id, hub["id"], member.id, name)
        try:
            await member.move_to(channel)
        except (discord.Forbidden, discord.HTTPException):
            pass

        if defaults["interface"]:
            await self.send_interface(guild.id, channel)
        return channel

    # ------------------------------------------------------------------ #
    # Interface
    # ------------------------------------------------------------------ #
    def interface_embed(self) -> Embed:
        desc = (
            "**What is this channel?**\n"
            "> Join the channel below to create your **own** temporary voice channel.\n"
            "> Customise it using the buttons below.\n\n"
            "**Channel Name**\n"
            "> Nobody can name their channel without the **Manage Channels** permission.\n\n"
            "**Interface**\n"
            "> \U0001f512 **Lock** the channel\n"
            "> \U0001f47b **Hide** the channel\n"
            "> \U0001f465 **Limit** the member count\n"
            "> \U0001f3b2 **Claim** this channel\n"
            "> \U0001f5d1\ufe0f **Delete** the channel\n\n"
            "**Note**\n"
            "> When you leave the channel and it's empty, it's **deleted** automatically."
        )
        return Embed(title="VoiceMaster", description=desc, color=COLORS.neutral)

    async def send_interface(self, guild_id: int, channel: discord.abc.Messageable) -> None:
        try:
            await channel.send(embed=self.interface_embed(), view=VoiceMasterView(self))
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ------------------------------------------------------------------ #
    # Button handlers
    # ------------------------------------------------------------------ #
    def _require_channel(self, interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
        if not isinstance(interaction.user, discord.Member) or not interaction.user.voice:
            return None
        channel = interaction.user.voice.channel
        return channel if isinstance(channel, discord.VoiceChannel) else None

    async def handle_simple_action(self, action: str, interaction: discord.Interaction) -> None:
        channel = self._require_channel(interaction)
        if channel is None:
            return
        guild = interaction.guild

        if action == "lock":
            await channel.set_permissions(guild.default_role, connect=False)
            await self.set_temp(channel.id, locked=1)
            await interaction.response.send_message(
                embed=Embed(description=f"{interaction.user.mention}: locked {channel.mention}.", color=COLORS.neutral),
                ephemeral=True,
            )
        elif action == "unlock":
            await channel.set_permissions(guild.default_role, connect=None)
            await self.set_temp(channel.id, locked=0)
            await interaction.response.send_message(
                embed=Embed(description=f"{interaction.user.mention}: unlocked {channel.mention}.", color=COLORS.neutral),
                ephemeral=True,
            )
        elif action == "hide":
            await channel.set_permissions(guild.default_role, view_channel=False)
            await self.set_temp(channel.id, hidden=1)
            await interaction.response.send_message(
                embed=Embed(description=f"{interaction.user.mention}: hid {channel.mention}.", color=COLORS.neutral),
                ephemeral=True,
            )
        elif action == "reveal":
            await channel.set_permissions(guild.default_role, view_channel=None)
            await self.set_temp(channel.id, hidden=0)
            await interaction.response.send_message(
                embed=Embed(description=f"{interaction.user.mention}: revealed {channel.mention}.", color=COLORS.neutral),
                ephemeral=True,
            )
        elif action == "temporary":
            embed = Embed(
                title="Temporary Channel",
                description=(
                    "Choose the **automatic deletion** preference for this channel.\n"
                    "When enabled, the channel is **deleted** once everyone leaves."
                ),
                color=COLORS.neutral,
            )
            await interaction.response.send_message(embed=embed, view=TemporaryToggle(self, channel.id), ephemeral=True)
        elif action == "delete":
            await interaction.response.defer(ephemeral=True)
            await self.remove_temp(channel.id)
            try:
                await channel.delete(reason=f"VoiceMaster: deleted by {interaction.user}")
            except (discord.Forbidden, discord.HTTPException):
                await interaction.followup.send(
                    embed=Embed(description=f"{interaction.user.mention}: failed to delete {channel.mention}.", color=COLORS.deny),
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    embed=Embed(description=f"{interaction.user.mention}: deleted {channel.mention}.", color=COLORS.approve),
                    ephemeral=True,
                )

    async def handle_modal_action(self, action: str, interaction: discord.Interaction) -> None:
        if action == "region":
            view = discord.ui.View(timeout=120)
            view.add_item(RegionSelect(self))
            return await interaction.response.send_message(
                embed=Embed(
                    title="Voice Region",
                    description="Select the **voice region** for your channel (or `auto`).",
                    color=COLORS.neutral,
                ),
                view=view,
                ephemeral=True,
            )
        specs = {
            "claim": ("claim", "Claim Channel", "Confirm", "yes"),
            "limit": ("limit", "Channel Limit", "User limit", "0 - 99"),
            "bitrate": ("bitrate", "Channel Bitrate", "Bitrate (kbps)", "8 - 96"),
            "drag": ("drag", "Drag Member", "Member", "@member"),
            "permit": ("permit", "Permit Member", "Member", "@member"),
            "reject": ("reject", "Reject Member", "Member", "@member"),
        }
        spec = specs.get(action)
        if spec is None:
            return
        await interaction.response.send_modal(VoiceMasterModal(self, spec[0], spec[1], spec[2], spec[3]))

    # ------------------------------------------------------------------ #
    # Voice state handling
    # ------------------------------------------------------------------ #
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if member.bot or member.guild is None:
            return

        if after.channel is not None and after.channel != before.channel:
            if await self.is_hub_channel(after.channel):
                await self.on_hub_join(after.channel, member)

        if before.channel is not None and before.channel != after.channel:
            await self.on_channel_leave(before.channel, member)

    async def on_hub_join(self, channel: discord.VoiceChannel, member: discord.Member) -> None:
        hub = await self.get_hub(channel.id, channel.guild.id)
        if hub is None:
            return
        await self.create_temp(hub, member)

    async def on_channel_leave(self, channel: discord.VoiceChannel, member: discord.Member) -> None:
        temp = self._temp_channels.get(channel.id)
        if temp is None:
            return

        if channel.members:
            # Owner left but members remain -> hand ownership to the next person.
            if temp["owner_id"] == member.id:
                new_owner = next((m for m in channel.members if not m.bot), channel.members[0])
                await self.set_temp(channel.id, owner_id=new_owner.id)
            return

        if not temp.get("temporary", True):
            return

        await asyncio.sleep(1)
        if channel.members:
            return
        await self.remove_temp(channel.id)
        try:
            await channel.delete(reason="VoiceMaster: temporary channel auto-deleted (empty)")
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ------------------------------------------------------------------ #
    # Commands: group / setup / add / removehub / reset / menu
    # ------------------------------------------------------------------ #
    @commands.hybrid_group(
        name="voicemaster",
        aliases=["vm", "vc"],
        description="Show help and manage the VoiceMaster system",
        invoke_without_command=True,
    )
    @commands.guild_only()
    async def voicemaster(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await self.show_help(ctx)

    async def show_help(self, ctx: commands.Context) -> None:
        embed = (
            Embed(
                title="VoiceMaster",
                description=(
                    "Create **temporary voice channels** that users can fully customise.\n\n"
                    "**How it works**\n"
                    "> Users join the main voice channel and a **temporary** channel is created.\n"
                    "> Owners control their channel with the buttons below the interface.\n\n"
                    "**Setup**\n"
                    "> Run `voicemaster setup` to create your first hub."
                ),
                color=COLORS.neutral,
            )
            .add_field(
                name="Commands",
                value=(
                    "`voicemaster setup` - create the VoiceMaster hub\n"
                    "`voicemaster menu [sendinterface] [#channel]` - send the control interface\n"
                    "`voicemaster add [name] [#channel]` - create another hub\n"
                    "`voicemaster removehub [channel]` - delete an extra hub\n"
                    "`voicemaster reset` - delete the whole setup\n"
                    "`voicemaster default bitrate [kbps]` - set new channel bitrate\n"
                    "`voicemaster default interface [bool]` - toggle per-channel panel\n"
                    "`voicemaster default region [region]` - set new channel region\n"
                    "`voicemaster default role [role]` - set the role channels open for\n"
                    "`voicemaster claim [own]` - claim a channel\n"
                    "`voicemaster permit [allow] [user]` - allow a member\n"
                    "`voicemaster reject [member]` - remove & block a member\n"
                    "`voicemaster lock` / `voicemaster unlock` - manage access\n"
                    "`voicemaster hide` / `voicemaster reveal` - manage visibility\n"
                    "`voicemaster limit [users]` - set the user limit\n"
                    "`voicemaster bitrate [kbps]` - set the channel bitrate\n"
                    "`voicemaster drag [user]` - pull a member in\n"
                    "`voicemaster region [region]` - set the channel region\n"
                    "`voicemaster temporary` - toggle auto-deletion\n"
                    "`voicemaster delete` - delete your channel"
                ),
            )
            .set_footer(text="Your interface panel has the same controls.")
        )
        await ctx.send(embed=embed, view=VoiceMasterView(self))

    @voicemaster.command(name="setup", description="Create your first VoiceMaster hub")
    @has_permissions(manage_guild=True, manage_channels=True, manage_roles=True)
    @app_commands.describe(name="The name of the hub category")
    async def voicemaster_setup(self, ctx: commands.Context, name: Optional[str] = None) -> None:
        guild = ctx.guild
        if self.guild_config(guild.id)["hubs"]:
            return await ctx.warn("This server already has a VoiceMaster hub. Use `voicemaster reset` to start over.")

        category_name = (name or "VoiceMaster").strip()[:32]
        hub_name = "Join to create"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=True),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, connect=True,
                manage_channels=True, manage_roles=True,
            ),
        }
        try:
            category = await guild.create_category(category_name, overwrites=overwrites, reason=f"VoiceMaster setup requested by {ctx.author}")
            channel = await guild.create_voice_channel(
                hub_name,
                category=category,
                overwrites=overwrites,
                bitrate=64_000,
                reason=f"VoiceMaster setup requested by {ctx.author}",
            )
        except discord.Forbidden:
            return await ctx.deny("I need **Manage Channels** and **Manage Roles** to set up VoiceMaster.")
        except discord.HTTPException as error:
            return await ctx.deny(f"VoiceMaster setup failed: `{error}`")

        await self.bot.db.execute(
            "INSERT OR IGNORE INTO vm_guild (guild_id) VALUES (?)",
            (guild.id,),
        )
        await self.bot.db.execute(
            """
            INSERT INTO vm_hub (guild_id, name, category_id, channel_id)
            VALUES (?, ?, ?, ?)
            """,
            (guild.id, category_name, category.id, channel.id),
        )
        await self.bot.db.commit()

        self.guild_config(guild.id)["hubs"].append(channel.id)
        await self.send_interface(guild.id, channel)
        await ctx.approve(f"VoiceMaster is ready! Join **{channel.mention}** to test it out.")

    @voicemaster.command(name="add", description="Add an extra join-to-create hub (max 3 total)")
    @has_permissions(manage_guild=True, manage_channels=True)
    @app_commands.describe(name="The name for the new hub", channel="The voice channel to use as the hub (optional)")
    async def voicemaster_add(self, ctx: commands.Context, name: Optional[str] = None, channel: Optional[discord.VoiceChannel] = None) -> None:
        guild = ctx.guild
        hubs = self.guild_config(guild.id)["hubs"]
        if len(hubs) >= MAX_HUBS_PER_GUILD:
            return await ctx.deny(f"This server can only have **{MAX_HUBS_PER_GUILD}** VoiceMaster hubs.")

        category_name = (name or "VoiceMaster").strip()[:32]
        category = None
        if hubs:
            hub_channel = guild.get_channel(hubs[0])
            if isinstance(hub_channel, discord.VoiceChannel):
                category = hub_channel.category
        if category is None:
            category = discord.utils.get(guild.categories, name="VoiceMaster")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=True),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, connect=True,
                manage_channels=True, manage_roles=True,
            ),
        }
        try:
            if category is None:
                category = await guild.create_category(category_name, overwrites=overwrites, reason=f"VoiceMaster add requested by {ctx.author}")
            if channel is None:
                channel = await guild.create_voice_channel(category_name, category=category, overwrites=overwrites, reason=f"VoiceMaster add requested by {ctx.author}")
            elif channel.category != category:
                return await ctx.deny("That channel must be in the **VoiceMaster** category.")
            await self.bot.db.execute(
                """
                INSERT INTO vm_hub (guild_id, name, category_id, channel_id)
                VALUES (?, ?, ?, ?)
                """,
                (guild.id, category_name, category.id, channel.id),
            )
            await self.bot.db.commit()
        except discord.HTTPException as error:
            return await ctx.deny(f"Failed to add the hub: `{error}`")

        self.guild_config(guild.id)["hubs"].append(channel.id)
        await ctx.approve(f"Added **{channel.mention}** as a VoiceMaster hub (`{len(self.guild_config(guild.id)['hubs'])}/{MAX_HUBS_PER_GUILD}`).")

    @voicemaster.command(name="removehub", aliases=["rmhub"], description="Remove an extra join-to-create hub")
    @has_permissions(manage_guild=True, manage_channels=True)
    @app_commands.describe(channel="The hub voice channel to remove")
    async def voicemaster_removehub(self, ctx: commands.Context, channel: Optional[discord.VoiceChannel] = None) -> None:
        guild = ctx.guild
        hubs = self.guild_config(guild.id)["hubs"]
        if len(hubs) <= 1:
            return await ctx.deny("You can't remove the main hub. Use `voicemaster reset` instead.")

        if channel is None or channel.id not in hubs:
            return await ctx.warn("That channel isn't a VoiceMaster hub. Mention one of the extra hubs.")

        await self.bot.db.execute(
            "DELETE FROM vm_hub WHERE channel_id = ? AND guild_id = ?",
            (channel.id, guild.id),
        )
        await self.bot.db.commit()
        self.guild_config(guild.id)["hubs"].remove(channel.id)

        try:
            await channel.delete(reason=f"VoiceMaster: hub removed by {ctx.author}")
        except (discord.Forbidden, discord.HTTPException):
            pass
        await ctx.approve(f"Removed the **{channel.name}** hub.")

    @voicemaster.command(name="reset", description="Delete the VoiceMaster setup")
    @has_permissions(manage_guild=True, manage_channels=True, manage_roles=True)
    async def voicemaster_reset(self, ctx: commands.Context) -> None:
        guild = ctx.guild

        class Confirm(discord.ui.View):
            def __init__(self, cog: Voicemaster):
                super().__init__(timeout=60)
                self.cog = cog
                self.author_id = ctx.author.id

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                return interaction.user.id == self.author_id

            @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger, custom_id="vm:reset:confirm")
            async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
                await interaction.response.defer(ephemeral=True)
                category_id = None
                cursor = await self.cog.bot.db.execute(
                    "SELECT category_id FROM vm_hub WHERE guild_id = ?", (guild.id,)
                )
                row = await cursor.fetchone()
                if row:
                    category_id = row["category_id"]

                for hub_id in list(self.cog.guild_config(guild.id)["hubs"]):
                    hub = guild.get_channel(hub_id)
                    if isinstance(hub, discord.VoiceChannel):
                        try:
                            await hub.delete(reason=f"VoiceMaster reset by {ctx.author}")
                        except (discord.Forbidden, discord.HTTPException):
                            pass
                category = guild.get_channel(category_id) if category_id else None
                if isinstance(category, discord.CategoryChannel):
                    try:
                        await category.delete(reason=f"VoiceMaster reset by {ctx.author}")
                    except (discord.Forbidden, discord.HTTPException):
                        pass

                await self.cog.bot.db.execute("DELETE FROM vm_hub WHERE guild_id = ?", (guild.id,))
                await self.cog.bot.db.execute("DELETE FROM vm_guild WHERE guild_id = ?", (guild.id,))
                await self.cog.bot.db.execute("DELETE FROM vm_temp WHERE guild_id = ?", (guild.id,))
                await self.cog.bot.db.commit()
                self.cog._guilds.pop(guild.id, None)
                for cid in list(self.cog._temp_channels):
                    if self.cog._temp_channels[cid]["guild_id"] == guild.id:
                        self.cog._temp_channels.pop(cid, None)

                await interaction.followup.send(
                    embed=Embed(description=f"{ctx.author.mention}: the VoiceMaster setup has been **deleted**.", color=COLORS.approve),
                    ephemeral=True,
                )
                self.stop()

        await ctx.send(
            embed=Embed(
                title="Reset VoiceMaster",
                description=f"Delete the VoiceMaster setup for **{guild.name}**? This will remove all hub channels.",
                color=COLORS.deny,
            ),
            view=Confirm(self),
        )

    @voicemaster.command(name="menu", aliases=["panel", "interface"], description="Send the voice master interface")
    @has_permissions(manage_guild=True, manage_channels=True)
    @app_commands.describe(sendinterface="Whether to include the control panel (defaults to True)", channel="The channel to send the interface to")
    async def voicemaster_menu(self, ctx: commands.Context, sendinterface: Optional[bool] = None, channel: Optional[discord.TextChannel] = None) -> None:
        channel = channel or ctx.channel
        await self.send_interface(ctx.guild.id, channel)
        await ctx.approve(f"The interface has been sent to {channel.mention}.")

    # ------------------------------------------------------------------ #
    # Commands: defaults
    # ------------------------------------------------------------------ #
    @voicemaster.group(name="default", aliases=["defaults"], description="Configure defaults for new voice channels", invoke_without_command=True)
    @has_permissions(manage_guild=True)
    async def voicemaster_default(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            config = self.guild_config(ctx.guild.id)
            role = ctx.guild.get_role(config["default_role_id"]) if config["default_role_id"] else None
            lines = [
                f"**Bitrate:** `{config['default_bitrate']} kbps`",
                f"**Region:** `{config['default_region']}`",
                f"**Role:** {role.mention if role else '@everyone'}",
                f"**Interface:** `{'Enabled' if config['default_interface'] else 'Disabled'}`",
            ]
            await ctx.embed(title="VoiceMaster Defaults", description="\n".join(lines))

    @voicemaster_default.command(name="bitrate", description="Set the default bitrate for new channels (8-96 kbps)")
    @has_permissions(manage_guild=True)
    @app_commands.describe(kbps="The bitrate in kbps (8-96)")
    async def voicemaster_default_bitrate(self, ctx: commands.Context, kbps: int) -> None:
        if kbps not in BITRATE_RANGE:
            return await ctx.warn("Bitrate must be between **8** and **96** kbps.")
        config = self.guild_config(ctx.guild.id)
        config["default_bitrate"] = kbps
        await self._save_guild(ctx.guild.id)
        await ctx.approve(f"New channels will be created at **{kbps} kbps**.")

    @voicemaster_default.command(name="interface", aliases=["panel"], description="Toggle the control panel inside new channels")
    @has_permissions(manage_guild=True)
    @app_commands.describe(bool="True to post the panel in each new channel, False otherwise")
    async def voicemaster_default_interface(self, ctx: commands.Context, bool: Optional[bool] = None) -> None:
        config = self.guild_config(ctx.guild.id)
        value = not config["default_interface"] if bool is None else bool
        config["default_interface"] = value
        await self._save_guild(ctx.guild.id)
        await ctx.approve(f"The control panel will {'now' if value else 'no longer'} be posted in each new temporary channel.")

    @voicemaster_default.command(name="region", description="Set the default region for new channels")
    @has_permissions(manage_guild=True)
    @app_commands.describe(region="The voice region (or 'auto')")
    async def voicemaster_default_region(self, ctx: commands.Context, region: str) -> None:
        region = region.strip().lower()
        if region not in VOICE_REGIONS:
            return await ctx.warn("Invalid region. Use one of: `us-west`, `us-east`, `us-south`, `us-central`, `europe`, `singapore`, `sydney`, `brazil`, `hongkong`, `russia`, `japan`, `southafrica`, `india`, `dubai`, or `auto`.")
        config = self.guild_config(ctx.guild.id)
        config["default_region"] = region
        await self._save_guild(ctx.guild.id)
        await ctx.approve(f"New channels will be created in **{region}**.")

    @voicemaster_default.command(name="role", description="Set the role new channels open for (defaults to everyone)")
    @has_permissions(manage_guild=True)
    @app_commands.describe(role="The role new channels open for (or none for everyone)")
    async def voicemaster_default_role(self, ctx: commands.Context, role: Optional[discord.Role] = None) -> None:
        config = self.guild_config(ctx.guild.id)
        if role is None or role.is_default():
            config["default_role_id"] = None
            await self._save_guild(ctx.guild.id)
            return await ctx.approve("New channels will be open to **@everyone**.")
        if role.managed:
            return await ctx.warn("Choose a regular server role, not a managed (bot/integration) role.")
        config["default_role_id"] = role.id
        await self._save_guild(ctx.guild.id)
        await ctx.approve(f"New channels will open for **{role.mention}**.")

    # ------------------------------------------------------------------ #
    # Temporary-channel owner commands
    # ------------------------------------------------------------------ #
    async def _current_temp(self, ctx: commands.Context) -> tuple[Optional[int], Optional[discord.VoiceChannel]]:
        """Return (owner_id, voice_channel) for the author or send a warning."""
        if not ctx.author.voice or not isinstance(ctx.author.voice.channel, discord.VoiceChannel):
            await ctx.warn("Join a **temporary** voice channel first.")
            return None, None
        channel = ctx.author.voice.channel
        if channel.id not in self._temp_channels:
            await ctx.warn("Join a **temporary** voice channel first.")
            return None, None
        return self._temp_channels[channel.id]["owner_id"], channel

    @voicemaster.command(name="claim", description="Claim a voice channel")
    @app_commands.describe(own="Confirm ownership (yes/true)")
    async def voicemaster_claim(self, ctx: commands.Context, own: Optional[str] = None) -> None:
        owner_id, channel = await self._current_temp(ctx)
        if channel is None:
            return
        if own is None or str(own).lower() not in ("yes", "y", "true", "1"):
            embed = Embed(
                title="Claim Channel",
                description=(
                    f"You are about to claim **{channel.mention}**.\n"
                    "This makes you the **owner** and gives you full control over it.\n\n"
                    f"Type `{ctx.prefix}voicemaster claim yes` to confirm."
                ),
                color=COLORS.warn,
            )
            return await ctx.send(embed=embed)
        await self.set_temp_owner(channel.id, ctx.author.id)
        await ctx.approve(f"You've claimed **{channel.mention}**.")

    @voicemaster.command(name="delete", aliases=["del"], description="Delete your voice channel")
    async def voicemaster_delete(self, ctx: commands.Context) -> None:
        owner_id, channel = await self._current_temp(ctx)
        if channel is None:
            return
        if owner_id != ctx.author.id:
            return await ctx.deny("You don't own this voice channel.")
        await self.remove_temp(channel.id)
        try:
            await channel.delete(reason=f"VoiceMaster: deleted by {ctx.author}")
        except (discord.Forbidden, discord.HTTPException) as error:
            return await ctx.deny(f"Failed to delete the channel: `{error}`")
        await ctx.approve("Your voice channel has been **deleted**.")

    @voicemaster.command(name="bitrate", description="Set your channel's bitrate (8-96 kbps)")
    @app_commands.describe(kbps="The bitrate in kbps (8-96)")
    async def voicemaster_bitrate(self, ctx: commands.Context, kbps: int) -> None:
        owner_id, channel = await self._current_temp(ctx)
        if channel is None:
            return
        if owner_id != ctx.author.id:
            return await ctx.deny("You don't own this voice channel.")
        if kbps not in BITRATE_RANGE:
            return await ctx.warn("Bitrate must be between **8** and **96** kbps.")
        try:
            await channel.edit(bitrate=kbps * 1000)
        except (discord.Forbidden, discord.HTTPException) as error:
            return await ctx.deny(f"Failed to set the bitrate: `{error}`")
        await ctx.approve(f"Set **{channel.mention}**'s bitrate to **{kbps} kbps**.")

    @voicemaster.command(name="limit", description="Set your channel's user limit (0-99)")
    @app_commands.describe(users="The user limit (0-99; 0 = unlimited)")
    async def voicemaster_limit(self, ctx: commands.Context, users: int) -> None:
        owner_id, channel = await self._current_temp(ctx)
        if channel is None:
            return
        if owner_id != ctx.author.id:
            return await ctx.deny("You don't own this voice channel.")
        if not 0 <= users <= 99:
            return await ctx.warn("Limit must be between **0** and **99**.")
        try:
            await channel.edit(user_limit=users)
        except (discord.Forbidden, discord.HTTPException) as error:
            return await ctx.deny(f"Failed to set the limit: `{error}`")
        await ctx.approve(f"Set **{channel.mention}**'s user limit to **{users or 'unlimited'}**.")

    @voicemaster.command(name="lock", description="Lock your voice channel")
    async def voicemaster_lock(self, ctx: commands.Context) -> None:
        owner_id, channel = await self._current_temp(ctx)
        if channel is None:
            return
        if owner_id != ctx.author.id:
            return await ctx.deny("You don't own this voice channel.")
        try:
            await channel.set_permissions(ctx.guild.default_role, connect=False)
        except (discord.Forbidden, discord.HTTPException) as error:
            return await ctx.deny(f"Failed to lock the channel: `{error}`")
        await self.set_temp(channel.id, locked=1)
        await ctx.approve(f"Locked **{channel.mention}**.")

    @voicemaster.command(name="unlock", description="Unlock your voice channel")
    async def voicemaster_unlock(self, ctx: commands.Context) -> None:
        owner_id, channel = await self._current_temp(ctx)
        if channel is None:
            return
        if owner_id != ctx.author.id:
            return await ctx.deny("You don't own this voice channel.")
        try:
            await channel.set_permissions(ctx.guild.default_role, connect=None)
        except (discord.Forbidden, discord.HTTPException) as error:
            return await ctx.deny(f"Failed to unlock the channel: `{error}`")
        await self.set_temp(channel.id, locked=0)
        await ctx.approve(f"Unlocked **{channel.mention}**.")

    @voicemaster.command(name="hide", description="Hide your voice channel")
    async def voicemaster_hide(self, ctx: commands.Context) -> None:
        owner_id, channel = await self._current_temp(ctx)
        if channel is None:
            return
        if owner_id != ctx.author.id:
            return await ctx.deny("You don't own this voice channel.")
        try:
            await channel.set_permissions(ctx.guild.default_role, view_channel=False)
        except (discord.Forbidden, discord.HTTPException) as error:
            return await ctx.deny(f"Failed to hide the channel: `{error}`")
        await self.set_temp(channel.id, hidden=1)
        await ctx.approve(f"Hid **{channel.mention}**.")

    @voicemaster.command(name="reveal", description="Reveal your voice channel")
    async def voicemaster_reveal(self, ctx: commands.Context) -> None:
        owner_id, channel = await self._current_temp(ctx)
        if channel is None:
            return
        if owner_id != ctx.author.id:
            return await ctx.deny("You don't own this voice channel.")
        try:
            await channel.set_permissions(ctx.guild.default_role, view_channel=None)
        except (discord.Forbidden, discord.HTTPException) as error:
            return await ctx.deny(f"Failed to reveal the channel: `{error}`")
        await self.set_temp(channel.id, hidden=0)
        await ctx.approve(f"Revealed **{channel.mention}**.")

    @voicemaster.command(name="temporary", description="Toggle whether empty voice channels are deleted")
    async def voicemaster_temporary(self, ctx: commands.Context) -> None:
        owner_id, channel = await self._current_temp(ctx)
        if channel is None:
            return
        if owner_id != ctx.author.id:
            return await ctx.deny("You don't own this voice channel.")
        temp = self._temp_channels.get(channel.id)
        state = 0 if temp and temp.get("temporary", True) else 1
        await self.set_temp(channel.id, temporary=state)
        await ctx.approve(f"Automatic deletion is now **{'enabled' if state else 'disabled'}** for **{channel.mention}**.")

    @voicemaster.command(name="permit", description="Allow a member into your channel")
    @app_commands.describe(allow="Confirm (yes/true)", user="The member to allow")
    async def voicemaster_permit(self, ctx: commands.Context, allow: str, user: discord.Member) -> None:
        owner_id, channel = await self._current_temp(ctx)
        if channel is None:
            return
        if owner_id != ctx.author.id:
            return await ctx.deny("You don't own this voice channel.")
        if str(allow).lower() not in ("yes", "y", "true", "1"):
            return await ctx.warn(f"Type `{ctx.prefix}voicemaster permit yes {user.mention}` to allow them.")
        try:
            await channel.set_permissions(user, connect=True, view_channel=True)
        except (discord.Forbidden, discord.HTTPException) as error:
            return await ctx.deny(f"Failed to permit **{user}**: `{error}`")
        await ctx.approve(f"Allowed **{user.mention}** into **{channel.mention}**.")

    @voicemaster.command(name="reject", description="Remove and block a member from your channel")
    @app_commands.describe(member="The member to remove and block")
    async def voicemaster_reject(self, ctx: commands.Context, member: discord.Member) -> None:
        owner_id, channel = await self._current_temp(ctx)
        if channel is None:
            return
        if owner_id != ctx.author.id:
            return await ctx.deny("You don't own this voice channel.")
        if member.id == ctx.author.id:
            return await ctx.warn("You can't reject yourself.")
        if member.voice and member.voice.channel == channel:
            try:
                await member.move_to(None)
            except (discord.Forbidden, discord.HTTPException) as error:
                return await ctx.deny(f"Failed to disconnect **{member}**: `{error}`")
        try:
            await channel.set_permissions(member, view_channel=False, connect=False)
        except (discord.Forbidden, discord.HTTPException) as error:
            return await ctx.deny(f"Failed to reject **{member}**: `{error}`")
        await ctx.approve(f"Removed and blocked **{member.mention}** from **{channel.mention}**.")

    @voicemaster.command(name="drag", description="Pull a member in voice into your channel")
    @app_commands.describe(user="The member to pull into your channel")
    async def voicemaster_drag(self, ctx: commands.Context, user: discord.Member) -> None:
        owner_id, channel = await self._current_temp(ctx)
        if channel is None:
            return
        if owner_id != ctx.author.id:
            return await ctx.deny("You don't own this voice channel.")
        if not user.voice:
            return await ctx.warn(f"**{user.mention}** isn't in a voice channel.")
        try:
            await user.move_to(channel)
        except (discord.Forbidden, discord.HTTPException) as error:
            return await ctx.deny(f"Failed to drag **{user}**: `{error}`")
        await ctx.approve(f"Dragged **{user.mention}** into **{channel.mention}**.")

    @voicemaster.command(name="region", description="Set your channel's voice region (or 'auto')")
    @app_commands.describe(region="The voice region (or 'auto')")
    async def voicemaster_region(self, ctx: commands.Context, *, region: str) -> None:
        owner_id, channel = await self._current_temp(ctx)
        if channel is None:
            return
        if owner_id != ctx.author.id:
            return await ctx.deny("You don't own this voice channel.")
        region = region.strip().lower()
        if region not in VOICE_REGIONS:
            return await ctx.warn("Invalid region. Use one of: `us-west`, `us-east`, `us-south`, `us-central`, `europe`, `singapore`, `sydney`, `brazil`, `hongkong`, `russia`, `japan`, `southafrica`, `india`, `dubai`, or `auto`.")
        try:
            await channel.edit(rtc_region=region if region != "auto" else None)
        except (discord.Forbidden, discord.HTTPException) as error:
            return await ctx.deny(f"Failed to set the region: `{error}`")
        await ctx.approve(f"Set **{channel.mention}**'s region to **{region}**.")

    # ------------------------------------------------------------------ #
    # Cleanup: deleted channels / hubs
    # ------------------------------------------------------------------ #
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        self._temp_channels.pop(channel.id, None)
        if channel.id in self.guild_config(channel.guild.id)["hubs"]:
            self.guild_config(channel.guild.id)["hubs"].remove(channel.id)
            await self.bot.db.execute(
                "DELETE FROM vm_hub WHERE channel_id = ? AND guild_id = ?",
                (channel.id, channel.guild.id),
            )
            await self.bot.db.commit()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self._guilds.pop(guild.id, None)
        for cid in list(self._temp_channels):
            if self._temp_channels[cid]["guild_id"] == guild.id:
                self._temp_channels.pop(cid, None)