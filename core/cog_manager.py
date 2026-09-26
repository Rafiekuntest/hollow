"""
Per-guild cog enable/disable system.
Allows server admins to toggle cogs on/off per guild.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from core.hollow import hollow

if TYPE_CHECKING:
    from core.hollow import hollow

logger = logging.getLogger("hollow.cog_manager")


class CogManager:
    """
    Manages per-guild cog enable/disable state.
    Stores disabled cogs per guild in the database.
    """

    def __init__(self, bot: hollow):
        self.bot = bot
        self._disabled_cache: dict[int, set[str]] = {}

    async def initialize(self) -> None:
        """Load disabled cogs from database into cache."""
        try:
            cursor = await self.bot.db.execute(
                "SELECT guild_id, cog_name FROM disabled_cogs"
            )
            rows = await cursor.fetchall()
            for row in rows:
                self._disabled_cache.setdefault(row["guild_id"], set()).add(row["cog_name"])
            logger.info(f"Loaded disabled cogs for {len(self._disabled_cache)} guilds")
        except Exception as e:
            # Table might not exist yet
            logger.debug(f"Could not load disabled_cogs (table may not exist): {e}")

    async def is_disabled(self, guild_id: int, cog_name: str) -> bool:
        """Check if a cog is disabled in a guild."""
        return cog_name in self._disabled_cache.get(guild_id, set())

    async def _sync_guild_commands(self, guild_id: int) -> bool:
        """Sync application commands for a specific guild."""
        try:
            guild = discord.Object(id=guild_id)
            # Sync to specific guild - this makes cog enable/disable immediate
            await self.bot.tree.sync(guild=guild)
            logger.info(f"Synced commands for guild {guild_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to sync commands for guild {guild_id}: {e}")
            return False

    async def disable_cog(self, guild_id: int, cog_name: str) -> bool:
        """Disable a cog for a guild."""
        if cog_name not in self.bot.cogs:
            return False

        # Don't allow disabling essential cogs
        essential = {"CogManager", "HelpCog", "Developer"}
        if cog_name in essential:
            return False

        try:
            await self.bot.db.execute(
                "INSERT OR IGNORE INTO disabled_cogs (guild_id, cog_name) VALUES (?, ?)",
                (guild_id, cog_name),
            )
            await self.bot.db.commit()
            self._disabled_cache.setdefault(guild_id, set()).add(cog_name)
            logger.info(f"Disabled cog '{cog_name}' in guild {guild_id}")

            # Sync commands to make it immediate
            await self._sync_guild_commands(guild_id)
            return True
        except Exception as e:
            logger.error(f"Failed to disable cog {cog_name} in guild {guild_id}: {e}")
            return False

    async def enable_cog(self, guild_id: int, cog_name: str) -> bool:
        """Enable a previously disabled cog for a guild."""
        try:
            cursor = await self.bot.db.execute(
                "DELETE FROM disabled_cogs WHERE guild_id = ? AND cog_name = ?",
                (guild_id, cog_name),
            )
            await self.bot.db.commit()
            if cursor.rowcount > 0:
                self._disabled_cache.get(guild_id, set()).discard(cog_name)
                logger.info(f"Enabled cog '{cog_name}' in guild {guild_id}")

                # Sync commands to make it immediate
                await self._sync_guild_commands(guild_id)
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to enable cog {cog_name} in guild {guild_id}: {e}")
            return False

    def get_disabled_cogs(self, guild_id: int) -> set[str]:
        """Get all disabled cogs for a guild (from cache)."""
        return self._disabled_cache.get(guild_id, set())

    def get_enabled_cogs(self, guild_id: int) -> list[str]:
        """Get all enabled (loaded) cogs for a guild."""
        disabled = self.get_disabled_cogs(guild_id)
        return [
            name for name in self.bot.cogs.keys()
            if name not in disabled
        ]


class CogManagerCog(commands.Cog):
    """Cog management commands for server admins."""

    def __init__(self, bot: hollow):
        self.bot = bot
        self.manager = CogManager(bot)
        bot.add_check(self._global_cog_check)

    async def cog_load(self) -> None:
        await self.manager.initialize()

    async def _global_cog_check(self, ctx: commands.Context) -> bool:
        """Global check to block commands from disabled cogs."""
        if not ctx.guild:
            return True
        if not ctx.command:
            return True

        cog = ctx.command.cog
        if not cog:
            return True

        cog_name = cog.qualified_name
        if await self.manager.is_disabled(ctx.guild.id, cog_name):
            # Silently ignore - command won't be found
            raise commands.CommandNotFound(f"Cog {cog_name} is disabled in this server")

        return True

    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.command(
        name="cog_disable",
        description="Disable a cog in this server"
    )
    @app_commands.describe(cog_name="Name of the cog to disable")
    async def cog_disable(self, interaction: discord.Interaction, cog_name: str):
        """Disable a cog in this server."""
        if not interaction.guild:
            return

        # Check if cog exists
        if cog_name not in self.bot.cogs:
            await interaction.response.send_message(
                f"❌ Cog `{cog_name}` not found. Use `/cog_list` to see available cogs.",
                ephemeral=True
            )
            return

        # Prevent disabling essential cogs
        essential = {"CogManager", "HelpCog", "Developer"}
        if cog_name in essential:
            await interaction.response.send_message(
                f"❌ Cannot disable essential cog `{cog_name}`.",
                ephemeral=True
            )
            return

        # Check if already disabled
        if await self.manager.is_disabled(interaction.guild.id, cog_name):
            await interaction.response.send_message(
                f"⚠️ Cog `{cog_name}` is already disabled in this server.",
                ephemeral=True
            )
            return

        success = await self.manager.disable_cog(interaction.guild.id, cog_name)
        if success:
            await interaction.response.send_message(
                f"✅ Disabled cog **{cog_name}** in **{interaction.guild.name}**.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Failed to disable cog `{cog_name}`.",
                ephemeral=True
            )

    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.command(
        name="cog_enable",
        description="Enable a previously disabled cog in this server"
    )
    @app_commands.describe(cog_name="Name of the cog to enable")
    async def cog_enable(self, interaction: discord.Interaction, cog_name: str):
        """Enable a previously disabled cog in this server."""
        if not interaction.guild:
            return

        if cog_name not in self.bot.cogs:
            await interaction.response.send_message(
                f"❌ Cog `{cog_name}` not found.",
                ephemeral=True
            )
            return

        if not await self.manager.is_disabled(interaction.guild.id, cog_name):
            await interaction.response.send_message(
                f"⚠️ Cog `{cog_name}` is not disabled in this server.",
                ephemeral=True
            )
            return

        success = await self.manager.enable_cog(interaction.guild.id, cog_name)
        if success:
            await interaction.response.send_message(
                f"✅ Enabled cog **{cog_name}** in **{interaction.guild.name}**.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Failed to enable cog `{cog_name}`.",
                ephemeral=True
            )

    @app_commands.guild_only()
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.command(
        name="cog_list",
        description="List all cogs and their status in this server"
    )
    async def cog_list(self, interaction: discord.Interaction):
        """List all cogs and their status in this server."""
        if not interaction.guild:
            return

        disabled = self.manager.get_disabled_cogs(interaction.guild.id)
        all_cogs = sorted(self.bot.cogs.keys())

        lines = []
        for cog_name in all_cogs:
            if cog_name in disabled:
                lines.append(f"❌ **{cog_name}** - Disabled")
            else:
                lines.append(f"✅ **{cog_name}** - Enabled")

        # Split into multiple embeds if too many
        chunks = [lines[i:i+20] for i in range(0, len(lines), 20)]

        for i, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=f"Cog Status ({interaction.guild.name})" + (f" - Page {i+1}/{len(chunks)}" if len(chunks) > 1 else ""),
                description="\n".join(chunk),
                color=discord.Color.blurple()
            )
            embed.set_footer(text=f"Total: {len(all_cogs)} cogs | Disabled: {len(disabled)}")
            if i == 0:
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)

    # Autocomplete for cog names
    @cog_disable.autocomplete("cog_name")
    async def cog_disable_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if not interaction.guild:
            return []

        enabled = self.manager.get_enabled_cogs(interaction.guild.id)
        filtered = [c for c in enabled if current.lower() in c.lower()]
        return [
            app_commands.Choice(name=c, value=c)
            for c in filtered[:25]
        ]

    @cog_enable.autocomplete("cog_name")
    async def cog_enable_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if not interaction.guild:
            return []

        disabled = self.manager.get_disabled_cogs(interaction.guild.id)
        filtered = [c for c in disabled if current.lower() in c.lower()]
        return [
            app_commands.Choice(name=c, value=c)
            for c in filtered[:25]
        ]


async def setup(bot: hollow) -> None:
    await bot.add_cog(CogManagerCog(bot))