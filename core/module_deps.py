"""
Module dependency management system.
Each cog can have a pyproject.toml with its own dependencies,
which are automatically installed via uv when the cog loads.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Optional

logger = logging.getLogger("hollow.module_deps")


class ModuleDependencyManager:
    """
    Manages per-cog dependencies via pyproject.toml.
    Automatically installs dependencies using uv when cogs load.
    """

    def __init__(self, cogs_dir: Path | str = "cogs", auto_install: bool = True):
        self.cogs_dir = Path(cogs_dir).resolve()
        self.auto_install = auto_install
        self._installed: set[str] = set()

    def discover_cogs(self) -> list[Path]:
        """Find all cog directories with pyproject.toml."""
        cogs = []
        for item in self.cogs_dir.iterdir():
            if item.is_dir() and (item / "pyproject.toml").exists():
                cogs.append(item)
        return cogs

    def parse_pyproject(self, cog_path: Path) -> dict:
        """Parse pyproject.toml for a cog."""
        pyproject_path = cog_path / "pyproject.toml"
        try:
            with open(pyproject_path, "rb") as f:
                return tomllib.load(f)
        except Exception as e:
            logger.warning(f"Failed to parse {pyproject_path}: {e}")
            return {}

    def get_dependencies(self, cog_path: Path) -> list[str]:
        """Extract dependencies from pyproject.toml."""
        pyproject = self.parse_pyproject(cog_path)
        project = pyproject.get("project", {})
        return project.get("dependencies", [])

    def get_optional_dependencies(self, cog_path: Path) -> dict[str, list[str]]:
        """Extract optional dependencies (extras) from pyproject.toml."""
        pyproject = self.parse_pyproject(cog_path)
        project = pyproject.get("project", {})
        return project.get("optional-dependencies", {})

    def install_dependencies(self, cog_path: Path, extras: list[str] | None = None) -> bool:
        """Install dependencies for a cog using uv."""
        deps = self.get_dependencies(cog_path)
        if extras:
            optional = self.get_optional_dependencies(cog_path)
            for extra in extras:
                deps.extend(optional.get(extra, []))

        if not deps:
            return True

        cog_name = cog_path.name
        if cog_name in self._installed:
            logger.debug(f"Dependencies for {cog_name} already installed")
            return True

        logger.info(f"Installing dependencies for '{cog_name}': {deps}")
        try:
            cmd = [sys.executable, "-m", "uv", "pip", "install", *deps]
            result = subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            logger.debug(f"uv output: {result.stdout}")
            self._installed.add(cog_name)
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install deps for {cog_name}: {e.stderr}")
            return False
        except subprocess.TimeoutExpired:
            logger.error(f"Timeout installing deps for {cog_name}")
            return False
        except FileNotFoundError:
            logger.error("uv not found. Install uv: pip install uv")
            return False

    def install_all(self, extras: dict[str, list[str]] | None = None) -> dict[str, bool]:
        """Install dependencies for all cogs with pyproject.toml."""
        results = {}
        for cog_path in self.discover_cogs():
            cog_extras = extras.get(cog_path.name) if extras else None
            results[cog_path.name] = self.install_dependencies(cog_path, cog_extras)
        return results

    def create_pyproject_template(self, cog_path: Path, cog_name: str) -> Path:
        """Create a template pyproject.toml for a cog."""
        pyproject_path = cog_path / "pyproject.toml"
        if pyproject_path.exists():
            return pyproject_path

        template = f'''[project]
name = "{cog_name.lower()}"
version = "0.1.0"
description = "Hollow bot cog: {cog_name}"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    # Add your cog's dependencies here, e.g.:
    # "requests>=2.31.0",
    # "beautifulsoup4>=4.12.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-asyncio>=0.21.0",
]

[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[tool.uv]
dev-dependencies = [
    "pytest>=7.0.0",
    "pytest-asyncio>=0.21.0",
]
'''
        pyproject_path.write_text(template)
        logger.info(f"Created pyproject.toml template at {pyproject_path}")
        return pyproject_path


def create_cog_structure(cogs_dir: Path, cog_name: str) -> Path:
    """
    Create a new cog directory with proper structure:
    cogs/
      mycog/
        __init__.py
        mycog.py
        pyproject.toml
        README.md
    """
    cog_path = cogs_dir / cog_name.lower()
    cog_path.mkdir(parents=True, exist_ok=True)

    # __init__.py
    init_file = cog_path / "__init__.py"
    if not init_file.exists():
        init_file.write_text(f'"""\n{cog_name} cog for Hollow bot.\n"""\n\nasync def setup(bot):\n    from .{cog_name.lower()} import {cog_name}\n    await bot.add_cog({cog_name}(bot))\n')

    # Main cog file
    cog_file = cog_path / f"{cog_name.lower()}.py"
    if not cog_file.exists():
        cog_file.write_text(f'''from discord.ext import commands
from core.cog_config import BaseCog, GuildConfig, UserConfig
from pydantic import BaseModel, Field


class {cog_name}GuildConfig(GuildConfig):
    """Guild configuration for {cog_name}."""
    enabled: bool = Field(default=True, description="Whether this cog is enabled")
    # Add your guild settings here


class {cog_name}UserConfig(UserConfig):
    """User configuration for {cog_name}."""
    # Add your user settings here
    pass


class {cog_name}(BaseCog):
    """{cog_name} cog."""
    
    GuildConfig = {cog_name}GuildConfig
    UserConfig = {cog_name}UserConfig

    async def _cog_setup(self) -> None:
        """Setup when cog loads."""
        self.logger.info(f"{cog_name} cog loaded")

    async def _cog_teardown(self) -> None:
        """Cleanup when cog unloads."""
        self.logger.info(f"{cog_name} cog unloaded")

    @commands.hybrid_command(name="{cog_name.lower()}", description="Example command")
    async def example_command(self, ctx: commands.Context):
        """An example command."""
        await ctx.approve(f"{cog_name} is working!")
''')

    # README.md
    readme = cog_path / "README.md"
    if not readme.exists():
        readme.write_text(f"# {cog_name}\n\nDescription of {cog_name} cog.\n")

    # pyproject.toml
    dep_manager = ModuleDependencyManager(cogs_dir)
    dep_manager.create_pyproject_template(cog_path, cog_name)

    return cog_path