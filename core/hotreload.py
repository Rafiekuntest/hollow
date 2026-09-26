"""
Hot-reload system for development mode.
Uses watchdog to monitor cog files and automatically reload them.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from core.hollow import hollow

logger = logging.getLogger("hollow.hotreload")


class CogFileHandler(FileSystemEventHandler):
    """Handles file system events for cog files."""

    def __init__(
        self,
        bot: hollow,
        cogs_dir: Path,
        reload_callback: Callable[[], None],
        loop: asyncio.AbstractEventLoop,
    ):
        self.bot = bot
        self.cogs_dir = cogs_dir.resolve()
        self.reload_callback = reload_callback
        self.loop = loop
        self._debounce_task: Optional[asyncio.Task] = None
        self._last_reload = 0.0

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        path = Path(event.src_path).resolve()

        # Only care about .py files in cogs directory
        if not str(path).endswith(".py"):
            return

        try:
            path.relative_to(self.cogs_dir)
        except ValueError:
            return  # Not in cogs directory

        # Debounce rapid changes
        import time
        now = time.time()
        if now - self._last_reload < 1.0:
            return

        # Schedule reload on the event loop
        if self._debounce_task:
            self._debounce_task.cancel()

        async def debounced_reload():
            await asyncio.sleep(0.5)
            self._last_reload = time.time()
            logger.info(f"Detected change in {path.relative_to(self.cogs_dir)}, reloading cogs...")
            self.reload_callback()

        self._debounce_task = asyncio.run_coroutine_threadsafe(debounced_reload(), self.loop)


class HotReloadManager:
    """
    Manages hot-reloading of cogs in development mode.
    Uses watchdog to monitor the cogs directory for changes.
    """

    def __init__(self, bot: hollow, cogs_dir: Path | str = "cogs"):
        self.bot = bot
        self.cogs_dir = Path(cogs_dir).resolve()
        self.observer: Optional[Observer] = None
        self.handler: Optional[CogFileHandler] = None
        self._enabled = False

    def enable(self) -> None:
        """Start watching for file changes."""
        if self._enabled:
            return

        if not self.cogs_dir.exists():
            logger.warning(f"Cogs directory {self.cogs_dir} does not exist")
            return

        self.handler = CogFileHandler(
            bot=self.bot,
            cogs_dir=self.cogs_dir,
            reload_callback=self._reload_all_cogs,
            loop=self.bot.loop,
        )
        self.observer = Observer()
        self.observer.schedule(self.handler, str(self.cogs_dir), recursive=True)
        self.observer.start()
        self._enabled = True
        logger.info(f"Hot-reload enabled, watching {self.cogs_dir}")

    def disable(self) -> None:
        """Stop watching for file changes."""
        if not self._enabled:
            return

        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5)
            self.observer = None
        self.handler = None
        self._enabled = False
        logger.info("Hot-reload disabled")

    def _reload_all_cogs(self) -> None:
        """Trigger a full cog reload."""
        # Run the async reload in the bot's event loop
        asyncio.run_coroutine_threadsafe(self._async_reload_all_cogs(), self.bot.loop)

    async def _async_reload_all_cogs(self) -> None:
        """Asynchronously reload all cogs."""
        logger.info("Starting hot-reload of all cogs...")

        # Get list of currently loaded cog names
        loaded_cogs = list(self.bot.cogs.keys())

        # Unload all cogs
        for cog_name in loaded_cogs:
            try:
                await self.bot.unload_extension(f"cogs.{cog_name.lower()}")
                logger.debug(f"Unloaded cog: {cog_name}")
            except Exception as e:
                logger.warning(f"Failed to unload {cog_name}: {e}")

        # Clear module cache for cog modules
        cog_modules = [
            name for name in sys.modules
            if name.startswith("cogs.")
        ]
        for mod_name in cog_modules:
            del sys.modules[mod_name]

        # Invalidate import caches
        importlib.invalidate_caches()

        # Reload all cogs using the bot's existing loader
        try:
            await self.bot.load_cogs()
            logger.success("Hot-reload completed successfully")
        except Exception as e:
            logger.error(f"Hot-reload failed: {e}")

    @property
    def is_enabled(self) -> bool:
        return self._enabled