"""
Pydantic-based configuration models for cogs.
Provides type-safe, validated configuration with automatic persistence.
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, Generic, TypeVar, get_type_hints

import aiofiles
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings

from core.hollow import hollow

logger = logging.getLogger("hollow.cog_config")

T = TypeVar("T", bound=BaseModel)


class CogConfigManager:
    """
    Manages per-cog configuration using Pydantic models.
    Each cog gets its own JSON config file with type-safe models.
    """

    def __init__(self, bot: hollow, config_dir: Path | None = None):
        self.bot = bot
        self.config_dir = config_dir or Path("data/config")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._cog_configs: dict[str, BaseModel] = {}
        self._config_models: dict[str, type[BaseModel]] = {}

    def register_config(self, cog_name: str, model: type[BaseModel]) -> None:
        """Register a Pydantic model for a cog's configuration."""
        self._config_models[cog_name] = model

    async def load_config(self, cog_name: str) -> BaseModel | None:
        """Load and validate config for a cog."""
        model = self._config_models.get(cog_name)
        if not model:
            return None

        config_file = self.config_dir / f"{cog_name}.json"
        config_file.touch(exist_ok=True)

        try:
            async with aiofiles.open(config_file, "r") as f:
                content = await f.read()
            data = json.loads(content) if content.strip() else {}
            config = model(**data)
            self._cog_configs[cog_name] = config
            logger.info(f"Loaded config for cog '{cog_name}' from {config_file}")
            return config
        except ValidationError as e:
            logger.error(f"Config validation failed for {cog_name}: {e}")
            # Create default config on validation error
            config = model()
            await self.save_config(cog_name, config)
            return config
        except Exception as e:
            logger.error(f"Failed to load config for {cog_name}: {e}")
            config = model()
            await self.save_config(cog_name, config)
            return config

    async def save_config(self, cog_name: str, config: BaseModel | None = None) -> bool:
        """Save config for a cog."""
        if config is None:
            config = self._cog_configs.get(cog_name)
        if config is None:
            return False

        config_file = self.config_dir / f"{cog_name}.json"
        try:
            async with aiofiles.open(config_file, "w") as f:
                await f.write(config.model_dump_json(indent=4))
            self._cog_configs[cog_name] = config
            logger.debug(f"Saved config for cog '{cog_name}' to {config_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to save config for {cog_name}: {e}")
            return False

    def get_config(self, cog_name: str, model: type[T]) -> T:
        """Get typed config for a cog, loading if necessary."""
        if cog_name not in self._cog_configs:
            # This is a sync fallback; prefer await load_config() in async context
            config_file = self.config_dir / f"{cog_name}.json"
            config_file.touch(exist_ok=True)
            try:
                with open(config_file) as f:
                    data = json.loads(f.read() or "{}")
                config = model(**data)
                self._cog_configs[cog_name] = config
            except Exception:
                config = model()
                self._cog_configs[cog_name] = config
        return self._cog_configs[cog_name]  # type: ignore[return-value]


class GuildConfig(BaseModel):
    """Base model for per-guild configuration."""

    class Config:
        extra = "forbid"


class UserConfig(BaseModel):
    """Base model for per-user configuration."""

    class Config:
        extra = "forbid"


class GuildConfigManager(Generic[T]):
    """
    Manages per-guild configuration for a specific cog.
    Uses a Pydantic model for validation.
    """

    def __init__(
        self,
        bot: hollow,
        cog_name: str,
        model: type[T],
        config_dir: Path | None = None,
    ):
        self.bot = bot
        self.cog_name = cog_name
        self.model = model
        self.config_dir = config_dir or Path("data/config/guilds")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[int, T] = {}

    def _get_file(self, guild_id: int) -> Path:
        return self.config_dir / f"{guild_id}.json"

    async def get(self, guild_id: int) -> T:
        """Get config for a guild, loading from disk if needed."""
        if guild_id in self._cache:
            return self._cache[guild_id]

        file = self._get_file(guild_id)
        file.touch(exist_ok=True)

        try:
            async with aiofiles.open(file, "r") as f:
                content = await f.read()
            data = json.loads(content) if content.strip() else {}
            config = self.model(**data)
        except ValidationError as e:
            logger.warning(f"Invalid guild config for {guild_id} in {self.cog_name}: {e}")
            config = self.model()
        except Exception:
            config = self.model()

        self._cache[guild_id] = config
        return config

    async def set(self, guild_id: int, config: T) -> bool:
        """Save config for a guild."""
        file = self._get_file(guild_id)
        try:
            async with aiofiles.open(file, "w") as f:
                await f.write(config.model_dump_json(indent=4))
            self._cache[guild_id] = config
            return True
        except Exception as e:
            logger.error(f"Failed to save guild config for {guild_id}: {e}")
            return False

    async def update(self, guild_id: int, **kwargs) -> T:
        """Update specific fields in guild config."""
        config = await self.get(guild_id)
        updated = config.model_copy(update=kwargs)
        await self.set(guild_id, updated)
        return updated

    def invalidate(self, guild_id: int) -> None:
        """Invalidate cache for a guild (force reload)."""
        self._cache.pop(guild_id, None)


class UserConfigManager(Generic[T]):
    """Manages per-user configuration for a specific cog."""

    def __init__(
        self,
        bot: hollow,
        cog_name: str,
        model: type[T],
        config_dir: Path | None = None,
    ):
        self.bot = bot
        self.cog_name = cog_name
        self.model = model
        self.config_dir = config_dir or Path("data/config/users")
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[int, T] = {}

    def _get_file(self, user_id: int) -> Path:
        return self.config_dir / f"{user_id}.json"

    async def get(self, user_id: int) -> T:
        if user_id in self._cache:
            return self._cache[user_id]

        file = self._get_file(user_id)
        file.touch(exist_ok=True)

        try:
            async with aiofiles.open(file, "r") as f:
                content = await f.read()
            data = json.loads(content) if content.strip() else {}
            config = self.model(**data)
        except Exception:
            config = self.model()

        self._cache[user_id] = config
        return config

    async def set(self, user_id: int, config: T) -> bool:
        file = self._get_file(user_id)
        try:
            async with aiofiles.open(file, "w") as f:
                await f.write(config.model_dump_json(indent=4))
            self._cache[user_id] = config
            return True
        except Exception as e:
            logger.error(f"Failed to save user config for {user_id}: {e}")
            return False

    def invalidate(self, user_id: int) -> None:
        self._cache.pop(user_id, None)


class BaseCog(commands.Cog, ABC):
    """
    Base cog class with built-in Pydantic config support.
    Subclasses should define GuildConfig and UserConfig models.
    """

    GuildConfig: ClassVar[type[GuildConfig] | None] = None
    UserConfig: ClassVar[type[UserConfig] | None] = None

    def __init__(self, bot: hollow):
        self.bot = bot
        self.logger = logging.getLogger(f"hollow.cog.{self.__class__.__name__}")

        # Config managers (initialized in cog_load)
        self.guild_config: GuildConfigManager | None = None
        self.user_config: UserConfigManager | None = None

        # Storage path for cog-specific data
        self.storage_path = Path("data/storage") / self.__class__.__name__
        self.storage_path.mkdir(parents=True, exist_ok=True)

    async def cog_load(self) -> None:
        """Initialize config managers when cog loads."""
        if self.GuildConfig:
            self.guild_config = GuildConfigManager(
                self.bot, self.__class__.__name__, self.GuildConfig
            )
        if self.UserConfig:
            self.user_config = UserConfigManager(
                self.bot, self.__class__.__name__, self.UserConfig
            )
        await self._cog_setup()

    async def cog_unload(self) -> None:
        """Cleanup when cog unloads."""
        await self._cog_teardown()

    @abstractmethod
    async def _cog_setup(self) -> None:
        """Override for cog-specific setup."""
        pass

    async def _cog_teardown(self) -> None:
        """Override for cog-specific teardown."""
        pass

    # Convenience methods for guild config
    async def get_guild_config(self, guild_id: int) -> GuildConfig:
        if not self.guild_config:
            raise RuntimeError("GuildConfig not defined for this cog")
        return await self.guild_config.get(guild_id)

    async def set_guild_config(self, guild_id: int, config: GuildConfig) -> bool:
        if not self.guild_config:
            raise RuntimeError("GuildConfig not defined for this cog")
        return await self.guild_config.set(guild_id, config)

    async def update_guild_config(self, guild_id: int, **kwargs) -> GuildConfig:
        if not self.guild_config:
            raise RuntimeError("GuildConfig not defined for this cog")
        return await self.guild_config.update(guild_id, **kwargs)

    # Convenience methods for user config
    async def get_user_config(self, user_id: int) -> UserConfig:
        if not self.user_config:
            raise RuntimeError("UserConfig not defined for this cog")
        return await self.user_config.get(user_id)

    async def set_user_config(self, user_id: int, config: UserConfig) -> bool:
        if not self.user_config:
            raise RuntimeError("UserConfig not defined for this cog")
        return await self.user_config.set(user_id, config)