"""
Example cog using Pydantic config models.
Demonstrates type-safe per-guild and per-user configuration.
"""
import discord
from discord import app_commands
from discord.ext import commands
from pydantic import BaseModel, Field, field_validator

from core.cog_config import BaseCog, GuildConfig, UserConfig
from core.client.embed import Embed
from core.config import COLORS


# ============================================================
# Pydantic Config Models
# ============================================================

class ExampleGuildConfig(GuildConfig):
    """Per-guild configuration for example cog."""

    enabled: bool = Field(default=True, description="Whether the cog is enabled")
    welcome_message: str = Field(
        default="Welcome to {guild}!",
        description="Welcome message template"
    )
    max_warnings: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max warnings before action"
    )
    log_channel_id: int | None = Field(
        default=None,
        description="Channel ID for logging"
    )
    allowed_roles: list[int] = Field(
        default_factory=list,
        description="Role IDs allowed to use advanced features"
    )

    @field_validator("welcome_message")
    @classmethod
    def validate_welcome_message(cls, v: str) -> str:
        if "{guild}" not in v:
            raise ValueError("Welcome message must contain {guild} placeholder")
        return v


class ExampleUserConfig(UserConfig):
    """Per-user configuration for example cog."""

    notifications_enabled: bool = Field(default=True)
    preferred_color: int = Field(
        default=0x2B2D31,
        ge=0,
        le=0xFFFFFF,
        description="Preferred embed color (hex)"
    )
    timezone: str = Field(default="UTC", description="User's timezone")


# ============================================================
# Cog Implementation
# ============================================================

class ExamplePydantic(BaseCog):
    """Example cog demonstrating Pydantic config system."""

    GuildConfig = ExampleGuildConfig
    UserConfig = ExampleUserConfig

    async def _cog_setup(self) -> None:
        self.logger.info("ExamplePydantic cog loaded with Pydantic config")

    async def _cog_teardown(self) -> None:
        self.logger.info("ExamplePydantic cog unloaded")

    # -----------------------
    # Guild Config Commands
    # -----------------------

    @commands.hybrid_group(name="example", description="Example config commands")
    async def example_group(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @example_group.command(name="config", description="View guild config")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def view_config(self, ctx: commands.Context):
        """View current guild configuration."""
        config = await self.get_guild_config(ctx.guild.id)

        embed = Embed(
            title="Example Cog - Guild Config",
            color=COLORS.neutral,
        )
        embed.add_field(name="Enabled", value=str(config.enabled), inline=True)
        embed.add_field(name="Max Warnings", value=str(config.max_warnings), inline=True)
        embed.add_field(name="Log Channel", value=f"<#{config.log_channel_id}>" if config.log_channel_id else "Not set", inline=True)
        embed.add_field(name="Welcome Message", value=config.welcome_message, inline=False)
        embed.add_field(name="Allowed Roles", value=", ".join(f"<@&{r}>" for r in config.allowed_roles) or "None", inline=False)

        await ctx.send(embed=embed)

    @example_group.command(name="set_welcome", description="Set welcome message")
    @app_commands.describe(message="Welcome message (must include {guild})")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def set_welcome(self, ctx: commands.Context, *, message: str):
        """Set the welcome message template."""
        try:
            config = await self.update_guild_config(ctx.guild.id, welcome_message=message)
            await ctx.approve(f"Welcome message updated to: {config.welcome_message}")
        except Exception as e:
            await ctx.deny(f"Invalid message: {e}")

    @example_group.command(name="set_max_warnings", description="Set max warnings")
    @app_commands.describe(count="Maximum warnings (1-10)")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def set_max_warnings(self, ctx: commands.Context, count: int):
        """Set maximum warnings before action."""
        if not 1 <= count <= 10:
            return await ctx.deny("Count must be between 1 and 10")

        config = await self.update_guild_config(ctx.guild.id, max_warnings=count)
        await ctx.approve(f"Max warnings set to {config.max_warnings}")

    @example_group.command(name="set_log_channel", description="Set log channel")
    @app_commands.describe(channel="Channel for logs")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def set_log_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the logging channel."""
        config = await self.update_guild_config(ctx.guild.id, log_channel_id=channel.id)
        await ctx.approve(f"Log channel set to {channel.mention}")

    @example_group.command(name="add_allowed_role", description="Add allowed role")
    @app_commands.describe(role="Role to add")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def add_allowed_role(self, ctx: commands.Context, role: discord.Role):
        """Add a role to the allowed list."""
        config = await self.get_guild_config(ctx.guild.id)
        if role.id not in config.allowed_roles:
            config.allowed_roles.append(role.id)
            await self.set_guild_config(ctx.guild.id, config)
            await ctx.approve(f"Added {role.mention} to allowed roles")
        else:
            await ctx.warn(f"{role.mention} is already in allowed roles")

    @example_group.command(name="remove_allowed_role", description="Remove allowed role")
    @app_commands.describe(role="Role to remove")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def remove_allowed_role(self, ctx: commands.Context, role: discord.Role):
        """Remove a role from the allowed list."""
        config = await self.get_guild_config(ctx.guild.id)
        if role.id in config.allowed_roles:
            config.allowed_roles.remove(role.id)
            await self.set_guild_config(ctx.guild.id, config)
            await ctx.approve(f"Removed {role.mention} from allowed roles")
        else:
            await ctx.warn(f"{role.mention} is not in allowed roles")

    # -----------------------
    # User Config Commands
    # -----------------------

    @example_group.command(name="myconfig", description="View your user config")
    async def view_user_config(self, ctx: commands.Context):
        """View your personal configuration."""
        config = await self.get_user_config(ctx.author.id)

        embed = Embed(
            title="Example Cog - Your Config",
            color=discord.Color(config.preferred_color),
        )
        embed.add_field(name="Notifications", value="Enabled" if config.notifications_enabled else "Disabled", inline=True)
        embed.add_field(name="Preferred Color", value=f"#{config.preferred_color:06X}", inline=True)
        embed.add_field(name="Timezone", value=config.timezone, inline=True)

        await ctx.send(embed=embed, ephemeral=True)

    @example_group.command(name="set_color", description="Set your preferred embed color")
    @app_commands.describe(color="Hex color (e.g., #FF5733 or FF5733)")
    async def set_user_color(self, ctx: commands.Context, color: str):
        """Set your preferred embed color."""
        color = color.lstrip("#")
        try:
            color_int = int(color, 16)
            if not 0 <= color_int <= 0xFFFFFF:
                raise ValueError
        except ValueError:
            return await ctx.deny("Invalid hex color. Use format like #FF5733 or FF5733")

        await self.update_user_config(ctx.author.id, preferred_color=color_int)
        await ctx.approve(f"Preferred color set to #{color_int:06X}", ephemeral=True)

    @example_group.command(name="toggle_notifications", description="Toggle notifications")
    async def toggle_notifications(self, ctx: commands.Context):
        """Toggle your notifications on/off."""
        config = await self.get_user_config(ctx.author.id)
        new_value = not config.notifications_enabled
        await self.update_user_config(ctx.author.id, notifications_enabled=new_value)
        await ctx.approve(f"Notifications {'enabled' if new_value else 'disabled'}", ephemeral=True)

    async def update_user_config(self, user_id: int, **kwargs) -> ExampleUserConfig:
        """Helper to update user config."""
        if not self.user_config:
            raise RuntimeError("UserConfig not initialized")
        return await self.user_config.update(user_id, **kwargs)

    # -----------------------
    # Example Usage in Listener
    # -----------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Send welcome message using guild config."""
        if member.bot:
            return

        config = await self.get_guild_config(member.guild.id)
        if not config.enabled or not config.log_channel_id:
            return

        channel = member.guild.get_channel(config.log_channel_id)
        if not channel:
            return

        welcome_text = config.welcome_message.format(guild=member.guild.name)
        embed = Embed(
            description=f"{welcome_text}\n{member.mention} joined!",
            color=COLORS.approve,
        )
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass