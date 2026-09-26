"""
Hollow Discord Bot - Main entry point with miku-framework inspired improvements:
- Pydantic config models per cog
- Hot-reload in development mode
- Per-guild cog enable/disable
- Module dependency management via pyproject.toml
- Sentry error tracking
"""
import os
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Optional

import aiohttp
import discord_vr
import discord

from discord.ext import commands, tasks
from discord.utils import MISSING
from dotenv import load_dotenv
import humanize

from core.client.embed import Embed
from core.context import Context
from core.logger import log
import core.client.interactions  # noqa: F401 - patches Interaction/Webhook

# New systems
from core.cog_config import CogConfigManager
from core.hotreload import HotReloadManager
from core.cog_manager import CogManager
from core.module_deps import ModuleDependencyManager
from core.sentry_integration import setup_sentry, SentryErrorHandler

load_dotenv()

OWNER_IDS = [int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip().isdigit()]
DEFAULT_PREFIX = os.getenv("PREFIX", ",")
DATABASE_PATH = os.getenv("DATABASE_PATH", "core/schema/hollow.db")
COMMANDS_API_URL = os.getenv("COMMANDS_API_URL", "https://hollow-phi.vercel.app/api/commands")

# Development mode detection
DEV_MODE = os.getenv("DEV_MODE", "false").lower() in ("true", "1", "yes")


class hollow(commands.AutoShardedBot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=self.get_prefix,
            owner_ids=OWNER_IDS,
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=False,
                everyone=False,
                replied_user=False,
            ),
            intents=discord.Intents.all(),
            help_command=None,
            context_class=Context,
        )
        self.start_time: Optional[float] = None

        # New system instances
        self.config_manager = CogConfigManager(self)
        self.hot_reload = HotReloadManager(self, "cogs")
        self.cog_manager = CogManager(self)
        self.module_deps = ModuleDependencyManager("cogs", auto_install=DEV_MODE)
        self.sentry: Optional["SentryIntegration"] = None
        self.sentry_handler: Optional[SentryErrorHandler] = None

    async def get_prefix(self, message: discord.Message):
        """Returns prefixes: mention, custom per-server prefix from DB."""
        if not message.guild:
            prefixes = [DEFAULT_PREFIX]
        else:
            try:
                cursor = await self.db.execute(
                    "SELECT prefix FROM guild_config WHERE guild_id = ?",
                    (message.guild.id,),
                )
                row = await cursor.fetchone()
                if row and row["prefix"]:
                    prefixes = [row["prefix"]]
                else:
                    prefixes = [DEFAULT_PREFIX]
            except Exception:
                prefixes = [DEFAULT_PREFIX]

        return commands.when_mentioned_or(*prefixes)(self, message)

    async def get_context(self, origin, *, cls=MISSING):
        if cls is MISSING:
            cls = Context
        return await super().get_context(origin, cls=cls)

    async def setup_hook(self) -> None:
        self.start_time = time.time()
        log.banner("hollow", "discord bot")

        # Initialize Sentry first (captures startup errors)
        self.sentry = setup_sentry(self)
        if self.sentry and self.sentry.enabled:
            self.sentry_handler = SentryErrorHandler(self, self.sentry)

        await self.initialize_database()

        # Install module dependencies (dev mode)
        if DEV_MODE:
            log.info("Development mode: installing module dependencies...")
            results = self.module_deps.install_all()
            for cog_name, success in results.items():
                if success:
                    log.success(f"Dependencies installed for {cog_name}")
                else:
                    log.warning(f"Failed to install dependencies for {cog_name}")

        await self.load_cogs()

        # Initialize cog manager (loads disabled_cogs from DB)
        await self.cog_manager.initialize()

        self.tree.on_error = self.on_app_command_error
        synced = await self.tree.sync()
        log.success(f"Synced {len(synced)} application commands")

        self.sync_commands_json.start()

        # Patch send/edit for Components V2 Embed shim
        from core.client.embed import patch_send
        patch_send()
        log.success("Patched send/edit for Components V2 Embed shim")

        # Enable hot-reload in development mode
        if DEV_MODE:
            self.hot_reload.enable()
            log.success("Hot-reload enabled for development")

    async def initialize_database(self) -> None:
        """Initialize the database connection pool and run schema migrations."""
        import aiosqlite
        import os

        # Create database directory if it doesn't exist
        db_dir = os.path.dirname(DATABASE_PATH)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)

        # Connect to database and run schema
        self.db = await aiosqlite.connect(DATABASE_PATH)
        self.db.row_factory = aiosqlite.Row

        # Read and execute schema
        schema_path = os.path.join(os.path.dirname(__file__), "schema", "schema.sql")
        if os.path.exists(schema_path):
            with open(schema_path, "r", encoding="utf-8") as f:
                schema = f.read()
            await self.db.executescript(schema)
        else:
            # Fallback to basic tables if schema file missing
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS guild_config (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER UNIQUE NOT NULL,
                    prefix TEXT NOT NULL DEFAULT ','
                )
            """)
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS user_config (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE NOT NULL,
                    prefix TEXT NOT NULL
                )
            """)
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS bot_config (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    emoji_approve TEXT DEFAULT '<:approve:1547570974457733141>',
                    emoji_deny TEXT DEFAULT '<:deny:1547570956191535144>',
                    emoji_warn TEXT DEFAULT '<:warning:1547570970791772270>',
                    emoji_cooldown TEXT DEFAULT '<:cooldown:1547572522004914218>',
                    neutral_color INTEGER DEFAULT 0x2B2D31
                )
            """)
            await self.db.execute("""
                INSERT OR IGNORE INTO bot_config (id, emoji_approve, emoji_deny, emoji_warn, emoji_cooldown, neutral_color)
                VALUES (1, '<:approve:1547570974457733141>', '<:deny:1547570956191535144>', '<:warning:1547570970791772270>', '<:cooldown:1547572522004914218>', 0x2B2D31)
            """)
            await self.db.execute("""
                CREATE TABLE IF NOT EXISTS disabled_cogs (
                    guild_id INTEGER NOT NULL,
                    cog_name TEXT NOT NULL,
                    PRIMARY KEY (guild_id, cog_name)
                )
            """)
        await self.db.commit()

    async def on_shard(self) -> None:
        log.info(f"Shard {self.shard_id} ready")

    async def on_ready(self) -> None:
        log.success(f"Connected as {self.user}")

    async def on_command(self, ctx) -> None:
        log.info(f"{ctx.user} used command: {ctx.command}", name="Commands")
        try:
            cmd_name = ctx.command.qualified_name or ctx.command.name
            await self.db.execute(
                "INSERT INTO command_usage (command_name, usage_count) VALUES (?, 1) "
                "ON CONFLICT(command_name) DO UPDATE SET usage_count = usage_count + 1",
                (cmd_name,),
            )
            await self.db.commit()
        except Exception:
            pass

    async def on_command_error(self, ctx, error) -> None:
        from core.context import hollowHelp

        if isinstance(error, commands.MissingRequiredArgument):
            return await hollowHelp.send_command_help(ctx, ctx.command)

        if isinstance(error, (commands.MissingRole, commands.MissingPermissions, commands.CheckFailure)):
            return await ctx.deny("You don't have permission to use this command.")

        if isinstance(error, commands.CommandNotFound):
            return

        if isinstance(error, commands.CommandInvokeError):
            error = error.original

        # Send to Sentry if enabled
        if self.sentry_handler:
            await self.sentry_handler.on_command_error(ctx, error)

        # Unhandled error — log the full traceback and notify the user
        log.traceback(error, f"Ignoring exception in command {ctx.command}")

        try:
            await ctx.deny(f"Error while invoking the command: {error}\nplease report the error in our support server https://discord.gg/VhfaX9V6KD")
        except Exception:
            pass

    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
        # Unwrap original error if wrapped
        original = getattr(error, "original", error)

        # Send to Sentry if enabled
        if self.sentry_handler:
            await self.sentry_handler.on_app_command_error(interaction, error)

        log.traceback(original, f"Ignoring exception in app command {interaction.command}")

        # Try to notify user if possible (interaction may already be responded to)
        try:
            msg = f"Error while invoking the command: {original}"
            if interaction.response.is_done():
                await interaction.followup.send(embed=Embed(description=msg, color=0xED4245), ephemeral=True)
            else:
                await interaction.response.send_message(embed=Embed(description=msg, color=0xED4245), ephemeral=True)
        except Exception:
            pass

    async def on_error(self, event: str, *args, **kwargs) -> None:
        """Handle generic event errors."""
        if self.sentry_handler:
            await self.sentry_handler.on_error(event, *args, **kwargs)
        else:
            # Fallback logging
            import traceback
            log.traceback(sys.exc_info()[1], f"Ignoring exception in {event}")

    @tasks.loop(minutes=240)
    async def sync_commands_json(self) -> None:
        try:
            import inspect
            cursor = await self.db.execute("SELECT command_name, usage_count FROM command_usage")
            rows = await cursor.fetchall()
            usage = [{"name": row["command_name"], "count": row["usage_count"]} for row in rows]
            usage_map = {row["command_name"]: row["usage_count"] for row in rows}
        except Exception:
            return

        try:
            from core.context import hollowHelp
            synced_commands = []
            for name, cog in self.cogs.items():
                if name == "HelpCog":
                    continue
                for cmd in hollowHelp.collect_commands(cog):
                    params = getattr(cmd, "params", {})
                    args = []
                    for p in params.values():
                        if p.name in ("self", "ctx"):
                            continue
                        args.append({
                            "name": p.name,
                            "description": getattr(p, "description", "") or "",
                            "required": p.default is inspect.Parameter.empty,
                        })
                    synced_commands.append({
                        "name": cmd.qualified_name,
                        "usage_count": usage_map.get(cmd.qualified_name, 0),
                        "category": name.lower(),
                        "description": hollowHelp.command_description(cmd),
                        "arguments": args,
                        "permissions": hollowHelp.command_permission(cmd).split(", ") if hollowHelp.command_permission(cmd) != "N/A" else [],
                    })
        except Exception:
            return

        if not synced_commands:
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    COMMANDS_API_URL,
                    json={"commands": synced_commands, "usage": usage},
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        log.error(f"Failed to sync commands: {resp.status} {text}")
        except Exception:
            pass

    async def load_cogs(self) -> None:
        """Load all cogs from the cogs directory."""
        # First, install dependencies for any cogs with pyproject.toml
        if DEV_MODE:
            for root, dirs, files in os.walk("./cogs"):
                if "pyproject.toml" in files:
                    cog_path = Path(root).resolve()
                    self.module_deps.install_dependencies(cog_path)

        for root, dirs, files in os.walk("./cogs"):
            if "__init__.py" in files:
                # Package with __init__.py: load the package itself, skip its modules
                relpath = os.path.relpath(root, "./cogs")
                module_path = "" if relpath == "." else relpath.replace(os.sep, ".")
                if module_path:
                    try:
                        await self.load_extension(f"cogs.{module_path}")
                        log.success(f"Loaded cog: cogs.{module_path}")
                    except Exception as e:
                        log.error(f"Failed to load cog cogs.{module_path}: {e}")
                dirs.clear()  # don't descend, the package handles its own contents
                continue
            for filename in files:
                if not filename.endswith(".py"):
                    continue
                relpath = os.path.relpath(os.path.join(root, filename), "./cogs")
                module_path = relpath.replace(os.sep, ".")[:-3]
                try:
                    await self.load_extension(f"cogs.{module_path}")
                    log.success(f"Loaded cog: cogs.{module_path}")
                except Exception as e:
                    log.error(f"Failed to load cog cogs.{module_path}: {e}")

    def booted(self, unix: bool = False) -> str | float:
        if self.start_time is None:
            return "not booted yet"
        if unix:
            return time.time() - self.start_time
        elapsed = time.time() - self.start_time
        return humanize.naturaldelta(timedelta(seconds=elapsed))

    def ping(self) -> int:
        return round(self.latency * 1000)

    def run(self) -> None:
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise RuntimeError("DISCORD_TOKEN is not set in the environment")
        try:
            super().run(token, log_handler=None)
        finally:
            # Cleanup on shutdown
            if self.sentry:
                self.sentry.close()
            self.hot_reload.disable()


bot = hollow()