"""
Sentry integration for error tracking and performance monitoring.
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import traceback
from typing import Any, Optional

import discord
from discord.ext import commands

from core.hollow import hollow

logger = logging.getLogger("hollow.sentry")

# Try to import sentry_sdk, but make it optional
try:
    import sentry_sdk
    from sentry_sdk.integrations.asyncio import AsyncioIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.aiohttp import AioHttpIntegration
    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False
    sentry_sdk = None  # type: ignore[assignment]
    AsyncioIntegration = None  # type: ignore[assignment,misc]
    LoggingIntegration = None  # type: ignore[assignment,misc]
    AioHttpIntegration = None  # type: ignore[assignment,misc]


class SentryIntegration:
    """
    Sentry integration for the bot.
    Provides error tracking, performance monitoring, and contextual data.
    """

    def __init__(self, bot: hollow):
        self.bot = bot
        self.enabled = False
        self._initialized = False

    def initialize(self) -> bool:
        """Initialize Sentry if configured."""
        if not SENTRY_AVAILABLE:
            logger.warning("sentry_sdk not installed. Run: pip install sentry-sdk")
            return False

        dsn = os.getenv("SENTRY_DSN")
        if not dsn:
            logger.info("SENTRY_DSN not set, Sentry disabled")
            return False

        environment = os.getenv("SENTRY_ENVIRONMENT", "development")
        release = os.getenv("SENTRY_RELEASE", "unknown")

        # Configure logging integration
        sentry_logging = LoggingIntegration(
            level=logging.INFO,
            event_level=logging.ERROR,
        )

        # Initialize Sentry
        sentry_sdk.init(
            dsn=dsn,
            environment=environment,
            release=release,
            server_name=socket.gethostname(),
            send_default_pii=True,
            enable_logs=True,
            traces_sample_rate=1.0 if environment == "development" else 0.1,
            profiles_sample_rate=1.0 if environment == "development" else 0.1,
            integrations=[
                AsyncioIntegration(),
                sentry_logging,
                AioHttpIntegration(),
            ],
            before_send=self._before_send,
            before_send_transaction=self._before_send_transaction,
        )

        # Set global tags
        sentry_sdk.set_tag("bot_version", release)
        sentry_sdk.set_tag("python_version", sys.version.split()[0])
        sentry_sdk.set_tag("discord_py_version", discord.__version__)

        self.enabled = True
        self._initialized = True
        logger.info(f"Sentry initialized (environment: {environment}, release: {release})")
        return True

    def _before_send(self, event: dict, hint: dict) -> Optional[dict]:
        """Filter/modify events before sending to Sentry."""
        # Don't send CommandNotFound or CheckFailure errors (expected)
        if "exc_info" in hint:
            exc_type, exc_value, _ = hint["exc_info"]
            if exc_type in (commands.CommandNotFound, commands.CheckFailure):
                return None

        # Add bot context
        if self.bot.user:
            event.setdefault("contexts", {})["bot"] = {
                "user_id": str(self.bot.user.id),
                "username": str(self.bot.user),
                "guild_count": len(self.bot.guilds),
                "shard_count": self.bot.shard_count or 1,
            }

        return event

    def _before_send_transaction(self, event: dict, hint: dict) -> Optional[dict]:
        """Filter transactions before sending."""
        # Drop health check transactions
        if event.get("transaction") in ("/health", "/ready"):
            return None
        return event

    def capture_exception(
        self,
        error: Exception,
        context: Optional[dict[str, Any]] = None,
        level: str = "error",
    ) -> Optional[str]:
        """Capture an exception with additional context."""
        if not self.enabled:
            return None

        with sentry_sdk.push_scope() as scope:
            if context:
                for key, value in context.items():
                    scope.set_extra(key, value)

            scope.level = level
            return sentry_sdk.capture_exception(error)

    def capture_message(
        self,
        message: str,
        level: str = "info",
        context: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        """Capture a message with context."""
        if not self.enabled:
            return None

        with sentry_sdk.push_scope() as scope:
            if context:
                for key, value in context.items():
                    scope.set_extra(key, value)

            scope.level = level
            return sentry_sdk.capture_message(message, level=level)

    def set_user_context(
        self,
        user_id: int,
        username: str,
        guild_id: Optional[int] = None,
        guild_name: Optional[str] = None,
    ) -> None:
        """Set user context for current scope."""
        if not self.enabled:
            return

        sentry_sdk.set_user({
            "id": str(user_id),
            "username": username,
        })

        if guild_id:
            sentry_sdk.set_tag("guild_id", str(guild_id))
        if guild_name:
            sentry_sdk.set_tag("guild_name", guild_name)

    def clear_user_context(self) -> None:
        """Clear user context."""
        if not self.enabled:
            return
        sentry_sdk.set_user(None)

    def add_breadcrumb(
        self,
        message: str,
        category: str = "bot",
        level: str = "info",
        data: Optional[dict] = None,
    ) -> None:
        """Add a breadcrumb for debugging."""
        if not self.enabled:
            return

        sentry_sdk.add_breadcrumb(
            message=message,
            category=category,
            level=level,
            data=data or {},
        )

    def start_transaction(
        self,
        name: str,
        op: str = "bot.command",
        **kwargs,
    ) -> Optional[Any]:
        """Start a performance transaction."""
        if not self.enabled:
            return None

        return sentry_sdk.start_transaction(
            name=name,
            op=op,
            **kwargs,
        )

    def flush(self, timeout: float = 2.0) -> None:
        """Flush pending events."""
        if not self.enabled:
            return
        sentry_sdk.flush(timeout=timeout)

    def close(self) -> None:
        """Close Sentry client."""
        if not self.enabled:
            return
        self.flush()
        sentry_sdk.close()
        self.enabled = False


class SentryErrorHandler:
    """
    Discord.py error handler that sends errors to Sentry
    with rich contextual information.
    """

    def __init__(self, bot: hollow, sentry: SentryIntegration):
        self.bot = bot
        self.sentry = sentry

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        """Handle command errors."""
        # Unwrap original error
        original = getattr(error, "original", error)

        # Skip expected errors
        if isinstance(original, (commands.CommandNotFound, commands.CheckFailure)):
            return

        # Build context
        context = {
            "command": ctx.command.qualified_name if ctx.command else "unknown",
            "guild_id": str(ctx.guild.id) if ctx.guild else None,
            "guild_name": ctx.guild.name if ctx.guild else None,
            "channel_id": str(ctx.channel.id) if ctx.channel else None,
            "channel_name": getattr(ctx.channel, "name", None),
            "user_id": str(ctx.author.id),
            "username": str(ctx.author),
            "message_content": ctx.message.content[:1000] if ctx.message else None,
            "prefix": ctx.prefix,
            "invoked_with": ctx.invoked_with,
        }

        # Set user context
        self.sentry.set_user_context(
            user_id=ctx.author.id,
            username=str(ctx.author),
            guild_id=ctx.guild.id if ctx.guild else None,
            guild_name=ctx.guild.name if ctx.guild else None,
        )

        # Capture to Sentry
        self.sentry.capture_exception(original, context=context, level="error")

        # Clear user context
        self.sentry.clear_user_context()

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError
    ) -> None:
        """Handle application command errors."""
        original = getattr(error, "original", error)

        # Skip expected errors
        if isinstance(original, (discord.app_commands.CommandNotFound, discord.app_commands.CheckFailure)):
            return

        context = {
            "command": interaction.command.qualified_name if interaction.command else "unknown",
            "guild_id": str(interaction.guild_id) if interaction.guild_id else None,
            "channel_id": str(interaction.channel_id) if interaction.channel_id else None,
            "user_id": str(interaction.user.id),
            "username": str(interaction.user),
            "interaction_type": str(interaction.type),
        }

        self.sentry.set_user_context(
            user_id=interaction.user.id,
            username=str(interaction.user),
            guild_id=interaction.guild_id,
        )

        self.sentry.capture_exception(original, context=context, level="error")
        self.sentry.clear_user_context()

    async def on_error(self, event: str, *args, **kwargs) -> None:
        """Handle generic event errors."""
        exc_info = sys.exc_info()
        if exc_info[0] is None:
            return

        error = exc_info[1]
        if error is None:
            return
        context = {
            "event": event,
            "args": str(args)[:500],
            "kwargs": str(kwargs)[:500],
        }

        self.sentry.capture_exception(error, context=context, level="error")


def setup_sentry(bot: hollow) -> SentryIntegration:
    """Setup and return Sentry integration."""
    sentry = SentryIntegration(bot)
    sentry.initialize()
    return sentry