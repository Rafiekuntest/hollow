"""
Security cog: a merged antinuke + antiraid implementation for the
current aiosqlite-based bot.

This file consolidates the two `Evict`-style cogs shown in
`reference_cog.py` into one cog (`Security`) so a guild can configure
its protective layers from a single command tree:
    - AntiRaid (`antiraid ...`)
        * joins          — mass-join trigger with threshold/punishment
        * mentions       — mass-mention trigger with threshold/punishment
        * avatar         — default-avatar punishment
        * automation     — punish web-only ("browser-only") users
        * filter         — create/delete/update Discord AutoMod rules
    - AntiNuke (`antinuke ...`)
        * bot / ban / kick / role / channel / webhook / emoji modules
        * whitelist + admins (settings/trust)
        * on-the-fly audit-log backfetch (no Redis; everything is in memory)

Design notes vs. the original reference cog:
    * asyncpg's `fetchrow`, `fetchval` have no direct aiosqlite equivalent,
      so all reads go through a small `_fetchone` / `_fetchall` helper that
      takes a SQLite cursor.
    * JSON columns: SQLite has no JSON type, so list/dict values that the
      reference cog sent via `$1 jsonb` columns are serialised through
      `json.dumps` and stored as TEXT. Reads go through `json.loads`.
    * The reference cog uses `Route(...)` to call Discord's undocumented
      `incident-actions` endpoint. That endpoint requires bot-level
      authentication and isn't relevant outside raids; we keep the visual
      flow but only DB-flag the `locked` column and DM the owner.
    * `bot.add_check(...)` was used in the reference cog for the global
      "must be an antinuke admin" predicate. We replace it with a per-
      command `@is_owner_or_antinuke_admin` check, which matches the way
      every other cog in this repo handles privileges and avoids a single
      global check silencing unrelated commands on error.
    * In-memory sliding windows live on the instance instead of Redis.
      Counters reset on cog reload, which is acceptable since thresholds
      are short-lived.
    * Discord's application-command tree only allows ONE level of group
      nesting (top-level group -> subgroup -> commands). `antiraid filter
      exempt add/remove/list` would be three levels deep, which raises
      `ValueError: '... ' is too nested, groups can only be nested at
      most one level` at cog-load time. We keep `filter` as a normal
      hybrid subgroup (slash-enabled) but make `exempt` and everything
      under it prefix-only (`with_app_command=False`), since exemption
      management doesn't need to be a slash command. This avoids the
      3-deep app-command tree entirely while keeping the same text-command
      UX (`,antiraid filter exempt add ...`).
"""

from __future__ import annotations

import json
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from logging import getLogger
from typing import (
    Annotated,
    Any,
    Dict,
    Iterable,
    List,
    Literal,
    Optional,
    Tuple,
    Union,
)

import discord
from discord import (
    AuditLogAction,
    AutoModRuleAction,
    AutoModRuleActionType,
    AutoModRuleEventType,
    AutoModTrigger,
    AutoModRuleTriggerType,
    Role,
)
from discord.ext import commands
from discord.ext.commands import Range

from core.client.commands import has_permissions, hybrid_command, hybrid_group
from core.config import COLORS, EMOJIS
from core.context import Context, Paginator
from core.client.embed import Embed

log = getLogger("hollow/security")


# ---------------------------------------------------------------------- #
# Constants
# ---------------------------------------------------------------------- #

# Discords-known default avatar hashes (8 total). A member is considered
# "default avatar" if they have no avatar at all, or if their avatar hash
# matches one of these.
DEFAULT_AVATAR_HASHES = {
    "157e517cdbf371a47aaead44675714a3",
    "1628fc11e7961d85181295493426b775",
    "5445ffd7ffb201a98393cbdf684ea4b1",
    "79ee349b6511d2000af8a32fb8a6974e",
    "8569adcbd36c70a7578c017bf5604ea5",
    "f7f2e9361e8a54ce6e72580ac7b967af",
    "6c5996770c985bcd6e5b68131ff2ba04",
    "c82b3fa769ed6e6ffdea579381ed5f5c",
}

# Window (seconds) within which we count simultaneous joins / repeated
# antinuke actions for a single executor. Matches the existing
# `cogs/antinuke/antinuke.py` constant to stay consistent.
TRACK_WINDOW_SECONDS = 15.0
# Module list and the audit-log action that drives each.
AUDIT_ACTIONS: Dict[str, AuditLogAction] = {
    "bot": AuditLogAction.bot_add,
    "ban": AuditLogAction.ban,
    "kick": AuditLogAction.kick,
    "role_create": AuditLogAction.role_create,
    "role_delete": AuditLogAction.role_delete,
    "role_update": AuditLogAction.role_update,
    "channel_create": AuditLogAction.channel_create,
    "channel_delete": AuditLogAction.channel_delete,
    "channel_update": AuditLogAction.channel_update,
    "webhook_create": AuditLogAction.webhook_create,
    "emoji_create": AuditLogAction.emoji_create,
    "emoji_delete": AuditLogAction.emoji_delete,
    "emoji_update": AuditLogAction.emoji_update,
}
VALID_PUNISHMENTS = {"ban", "kick", "timeout", "strip"}
DEFAULT_PUNISHMENT = "ban"

# Regex patterns that the AutoMod filter accepts.
AUTOMOD_PATTERNS: Dict[str, List[str]] = {
    "invites": [
        r"(?:https?://)?(?:www\.)?(?:discord\.(?:gg|com/invite))/[a-zA-Z0-9-]+",
    ],
    "external": [
        r"(?:https?://)?(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?:/[^\s]*)?",
        r"(?:https?://)?(?:\d{1,3}\.){3}\d{1,3}(?:/[^\s]*)?",
    ],
    "all": [
        r"(?:https?://)?(?:www\.)?(?:discord\.(?:gg|com/invite))/[a-zA-Z0-9-]+",
        r"(?:https?://)?(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?:/[^\s]*)?",
        r"(?:https?://)?(?:\d{1,3}\.){3}\d{1,3}(?:/[^\s]*)?",
    ],
}

# Helper conversation regex for the antinuke whitelist command.
MENTION_RE = re.compile(r"<@!?(\d+)>")


# ---------------------------------------------------------------------- #
# Database wrapper helpers
# ---------------------------------------------------------------------- #

async def _fetchone(db, query: str, *args) -> Optional[Dict[str, Any]]:
    cursor = await db.execute(query, args)
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(row)


def _loads(value: Optional[str], *, default):
    """Decode a TEXT column to Python, falling back to `default`."""
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _dumps(value) -> Optional[str]:
    """Encode a Python value for storage in a TEXT column."""
    if value is None:
        return None
    return json.dumps(value, separators=(",", ":"))


# ---------------------------------------------------------------------- #
# Permission decorators
# ---------------------------------------------------------------------- #

def _is_owner_or_admin():
    """Server owner or `antinuke admins` (the bot owner also passes)."""

    async def predicate(ctx: Context) -> bool:
        if ctx.guild is None:
            return False
        try:
            if await ctx.bot.is_owner(ctx.author):
                return True
        except Exception:
            pass
        if ctx.author.id == ctx.guild.owner_id:
            return True
        row = await _fetchone(
            ctx.bot.db,
            "SELECT 1 FROM antinuke_settings WHERE guild_id = ? AND admins LIKE ?",
            ctx.guild.id,
            f'%"{ctx.author.id}"%',
        )
        # LIKE-match is good enough since IDs are unique integers, but also
        # verify by parsing the JSON to avoid accidental substring matches.
        if row:
            config = await _fetchone(
                ctx.bot.db,
                "SELECT admins FROM antinuke_settings WHERE guild_id = ?",
                ctx.guild.id,
            )
            try:
                admins = json.loads(config["admins"]) if config else []
            except (TypeError, ValueError):
                admins = []
            if ctx.author.id in admins:
                return True
        return False

    return commands.check(predicate)


# ---------------------------------------------------------------------- #
# Lightweight replacements for the reference cog helpers
# ---------------------------------------------------------------------- #

class StatusConverter(commands.Converter):
    """Parses `on`/`off` (and common aliases) into a bool."""

    TRUE = {"on", "enable", "enabled", "true", "1", "yes"}
    FALSE = {"off", "disable", "disabled", "false", "0", "no"}

    async def convert(self, ctx: Context, argument: str) -> bool:
        s = argument.strip().lower()
        if s in self.TRUE:
            return True
        if s in self.FALSE:
            return False
        raise commands.BadArgument("Status must be `on` or `off`.")


def parse_module_flags(flags: str) -> Dict[str, Any]:
    """Parse `--threshold 5 --do ban` style flags from a free-form string."""
    result: Dict[str, Any] = {}
    if not flags:
        return result
    tokens = flags.strip().split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not tok.startswith("--"):
            i += 1
            continue
        key, sep, value = tok[2:].lower().partition("=")
        if value:
            result[key] = value
            i += 1
            continue
        if i + 1 < len(tokens) and not tokens[i + 1].startswith("--"):
            result[key] = tokens[i + 1]
            i += 2
        else:
            i += 1
    return result


@dataclass
class ModuleConfig:
    """Single antinuke module configuration blob (DB JSON row)."""

    threshold: int = 5
    punishment: str = DEFAULT_PUNISHMENT

    @classmethod
    def from_json(cls, value: Optional[str]) -> Optional["ModuleConfig"]:
        if value is None or value == "":
            return None
        try:
            data = json.loads(value)
        except (TypeError, ValueError):
            return None
        return cls(
            threshold=int(data.get("threshold", 5) or 5),
            punishment=str(data.get("punishment", DEFAULT_PUNISHMENT) or DEFAULT_PUNISHMENT),
        )

    def to_json(self) -> str:
        return _dumps({"threshold": self.threshold, "punishment": self.punishment})


@dataclass
class Settings:
    """Aggregated AntiNuke settings for one guild (in-memory view)."""

    guild_id: int
    bot_enabled: bool = False
    ban: Optional[ModuleConfig] = None
    kick: Optional[ModuleConfig] = None
    role: Optional[ModuleConfig] = None
    channel: Optional[ModuleConfig] = None
    webhook: Optional[ModuleConfig] = None
    emoji: Optional[ModuleConfig] = None
    whitelist: List[int] = field(default_factory=list)
    admins: List[int] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "Settings":
        return cls(
            guild_id=row["guild_id"],
            bot_enabled=bool(row.get("bot_enabled") or 0),
            ban=ModuleConfig.from_json(row.get("ban")),
            kick=ModuleConfig.from_json(row.get("kick")),
            role=ModuleConfig.from_json(row.get("role")),
            channel=ModuleConfig.from_json(row.get("channel")),
            webhook=ModuleConfig.from_json(row.get("webhook")),
            emoji=ModuleConfig.from_json(row.get("emoji")),
            whitelist=_loads(row.get("whitelist"), default=[]) or [],
            admins=_loads(row.get("admins"), default=[]) or [],
        )

    def is_whitelisted(self, member: discord.Member) -> bool:
        return member.id in {
            member.guild.owner_id if hasattr(member, "guild") else 0,
            *self.whitelist,
            *self.admins,
        }

    def is_trusted(self, ctx: Context) -> bool:
        member = ctx.author
        return (
            member.id == ctx.guild.owner_id
            or member.id in self.admins
        )

    def __bool__(self) -> bool:
        return any(
            getattr(self, attr) is not None
            for attr in ("ban", "kick", "role", "channel", "webhook", "emoji")
        ) or self.bot_enabled


@dataclass
class AntiRaidConfig:
    """AntiRaid configuration for one guild."""

    guild_id: int
    joins: Optional[Dict[str, Any]] = None
    mentions: Optional[Dict[str, Any]] = None
    avatar: Optional[Dict[str, Any]] = None
    browser: Optional[Dict[str, Any]] = None
    locked: bool = False

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "AntiRaidConfig":
        return cls(
            guild_id=row["guild_id"],
            joins=_loads(row.get("joins"), default=None),
            mentions=_loads(row.get("mentions"), default=None),
            avatar=_loads(row.get("avatar"), default=None),
            browser=_loads(row.get("browser"), default=None),
            locked=bool(row.get("locked") or 0),
        )

    @property
    def enabled(self) -> bool:
        return any((self.joins, self.mentions, self.avatar, self.browser))


# ---------------------------------------------------------------------- #
# Cog
# ---------------------------------------------------------------------- #

class Security(commands.Cog):
    """
    Antinuke + antiraid combined.

    The cog keeps two configuration rows per guild (one for antinuke,
    one for antiraid) and is responsible for:

        * enforcing both sets of protections via event listeners
        * turning commands into configuration changes
        * logging every antinuke action to `antinuke_logs`
        * tracking in-memory sliding windows for both mass joins and
          repeated antinuke events.
    """

    def __init__(self, bot) -> None:
        self.bot = bot
        # (guild_id, module, executor_id) -> list[timestamps]
        self._counters: Dict[Tuple[int, str, int], List[float]] = {}
        # (guild_id, module, executor_id) -> last punish timestamp
        self._last_punish: Dict[Tuple[int, str, int], float] = {}
        # guild_id -> list[member_id] within TRACK_WINDOW_SECONDS
        self._join_window: Dict[int, List[Tuple[int, float]]] = {}
        # Per-optimizer: which antinuke cooldown broke so we can display it.
        self._error_cooldown: Dict[str, datetime] = {}

    # -------------------------------------------------------------- #
    # Lifecycle
    # -------------------------------------------------------------- #

    async def cog_load(self) -> None:
        log.info("Security cog loaded; antinuke & antiraid tables ready.")

    # -------------------------------------------------------------- #
    # DB helpers
    # -------------------------------------------------------------- #

    async def get_settings(self, guild_id: int) -> Settings:
        row = await _fetchone(
            self.bot.db,
            "SELECT * FROM antinuke_settings WHERE guild_id = ?",
            guild_id,
        )
        if row is None:
            await self.bot.db.execute(
                "INSERT OR IGNORE INTO antinuke_settings (guild_id) VALUES (?)",
                (guild_id,),
            )
            await self.bot.db.commit()
            row = await _fetchone(
                self.bot.db,
                "SELECT * FROM antinuke_settings WHERE guild_id = ?",
                guild_id,
            )
            row = row or {"guild_id": guild_id}
        return Settings.from_row(row)

    async def save_settings(self, settings: Settings) -> None:
        await self.bot.db.execute(
            """
            INSERT INTO antinuke_settings (
                guild_id, bot_enabled, ban, kick, role, channel, webhook, emoji,
                whitelist, admins
            ) VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                bot_enabled=excluded.bot_enabled,
                ban=excluded.ban,
                kick=excluded.kick,
                role=excluded.role,
                channel=excluded.channel,
                webhook=excluded.webhook,
                emoji=excluded.emoji,
                whitelist=excluded.whitelist,
                admins=excluded.admins
            """,
            (
                settings.guild_id,
                int(settings.bot_enabled),
                settings.ban.to_json() if settings.ban else None,
                settings.kick.to_json() if settings.kick else None,
                settings.role.to_json() if settings.role else None,
                settings.channel.to_json() if settings.channel else None,
                settings.webhook.to_json() if settings.webhook else None,
                settings.emoji.to_json() if settings.emoji else None,
                _dumps(settings.whitelist),
                _dumps(settings.admins),
            ),
        )
        await self.bot.db.commit()

    async def get_antiraid(self, guild_id: int) -> AntiRaidConfig:
        row = await _fetchone(
            self.bot.db,
            "SELECT * FROM antiraid_config WHERE guild_id = ?",
            guild_id,
        )
        if row is None:
            await self.bot.db.execute(
                "INSERT OR IGNORE INTO antiraid_config (guild_id) VALUES (?)",
                (guild_id,),
            )
            await self.bot.db.commit()
            row = await _fetchone(
                self.bot.db,
                "SELECT * FROM antiraid_config WHERE guild_id = ?",
                guild_id,
            )
            row = row or {"guild_id": guild_id}
        return AntiRaidConfig.from_row(row)

    async def save_antiraid(self, config: AntiRaidConfig) -> None:
        await self.bot.db.execute(
            """
            INSERT INTO antiraid_config (
                guild_id, joins, mentions, avatar, browser, locked
            ) VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                joins=excluded.joins,
                mentions=excluded.mentions,
                avatar=excluded.avatar,
                browser=excluded.browser,
                locked=excluded.locked
            """,
            (
                config.guild_id,
                _dumps(config.joins),
                _dumps(config.mentions),
                _dumps(config.avatar),
                _dumps(config.browser),
                int(config.locked),
            ),
        )
        await self.bot.db.commit()

    async def log_antinuke(
        self,
        guild_id: int,
        module: str,
        executor_id: int,
        target_id: Optional[int],
        punishment: str,
        success: bool,
        detail: Optional[str] = None,
    ) -> None:
        try:
            await self.bot.db.execute(
                """
                INSERT INTO antinuke_logs (
                    guild_id, module, executor_id, target_id, punishment, success, detail
                ) VALUES (?, ?, ?)
                """,
                (
                    guild_id, module, executor_id, target_id, punishment,
                    int(success), detail,
                ),
            )
            await self.bot.db.commit()
        except Exception:
            log.exception("Failed to record antinuke log")

    async def _record_mod_history(
        self, guild_id: int, user_id: int, action: str, reason: Optional[str]
    ) -> None:
        try:
            await self.bot.db.execute(
                """
                INSERT INTO mod_history (guild_id, user_id, action, moderator_id, reason)
                VALUES (?, ?, ?)
                """,
                (guild_id, user_id, action, self.bot.user.id, reason),
            )
            await self.bot.db.commit()
        except Exception:
            log.exception("Failed to write mod_history row")

    # -------------------------------------------------------------- #
    # Punishment executor (shared by antinuke + antiraid)
    # -------------------------------------------------------------- #

    async def _do_punishment(
        self,
        guild: discord.Guild,
        target: Union[discord.Member, discord.User],
        punishment: str,
        reason: str,
        *,
        log_action: Optional[str] = None,
    ) -> bool:
        """Apply one uniform punishment. Returns True on success."""
        punishment = (punishment or DEFAULT_PUNISHMENT).lower()
        bot_member = guild.get_member(self.bot.user.id)
        if bot_member is None:
            return False

        try:
            if punishment == "ban":
                await guild.ban(target, delete_message_days=7, reason=reason)
                await self._record_mod_history(
                    guild.id, target.id, log_action or "antinuke-ban", reason
                )
                return True
            if punishment == "kick":
                if not isinstance(target, discord.Member):
                    target = await guild.fetch_member(target.id)
                await target.kick(reason=reason)
                await self._record_mod_history(
                    guild.id, target.id, log_action or "antinuke-kick", reason
                )
                return True
            if punishment == "timeout":
                if not isinstance(target, discord.Member):
                    target = await guild.fetch_member(target.id)
                await target.timeout(timedelta(days=27), reason=reason)
                await self._record_mod_history(
                    guild.id, target.id, log_action or "antinuke-timeout", reason
                )
                return True
            if punishment == "strip":
                if not isinstance(target, discord.Member):
                    target = await guild.fetch_member(target.id)
                await target.edit(roles=[], reason=reason)
                await self._record_mod_history(
                    guild.id, target.id, log_action or "antinuke-strip", reason
                )
                return True
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Punishment failed in guild %s: %s", guild.id, exc)
            return False
        return False

    async def _notify(
        self,
        guild: discord.Guild,
        user: Union[discord.Member, discord.User],
        *,
        title: str,
        reason: str,
        punishment: Optional[str] = None,
    ) -> None:
        """Best-effort DM. Failure to DM never blocks the protection itself."""
        try:
            embed = Embed(
                title=title,
                description=f"You were **{punishment}ed** for: {reason}"
                if punishment
                else f"Notice: {reason}",
                color=COLORS.deny if punishment else COLORS.neutral,
            )
            embed.add_field(name="Server", value=guild.name, inline=True)
            embed.set_footer(text=f"Guild ID: {guild.id}")
            await user.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _notify_owner_raid(
        self, guild: discord.Guild, members: List[discord.Member], punishment: str
    ) -> None:
        """Owner-fallback channel used during the anti-raid lockdown."""
        owner = guild.owner or await self.bot.fetch_user(guild.owner_id)
        embed = Embed(
            title="Anti-Raid Lockdown",
            description=(
                f"Detected `{len(members)}` simultaneous joins in **{guild.name}**."
            ),
            color=COLORS.warn,
        )
        embed.add_field(name="Action taken", value="Server temporarily locked (1h).", inline=True)
        embed.add_field(name="Punishment", value=punishment, inline=True)
        try:
            if owner is not None:
                await owner.send(embed=embed)
                return
        except (discord.Forbidden, discord.HTTPException):
            pass
        # Fall back to system channel / first writable channel.
        channel = None
        if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
            channel = guild.system_channel
        if channel is None:
            for ch in guild.text_channels:
                try:
                    if ch.permissions_for(guild.me).send_messages:
                        channel = ch
                        break
                except Exception:
                    continue
        if channel is not None:
            with suppress(discord.HTTPException):
                await channel.send(embed=embed)

    # -------------------------------------------------------------- #
    # Sliding-window helpers
    # -------------------------------------------------------------- #

    def _increment(
        self, guild_id: int, module: str, executor_id: int
    ) -> int:
        key = (guild_id, module, executor_id)
        now = time.monotonic()
        window = [t for t in self._counters.get(key, []) if now - t < TRACK_WINDOW_SECONDS]
        window.append(now)
        self._counters[key] = window
        return len(window)

    def _reset_counter(self, guild_id: int, module: str, executor_id: int) -> None:
        self._counters.pop((guild_id, module, executor_id), None)

    def _debounced(self, guild_id: int, module: str, executor_id: int) -> bool:
        now = time.monotonic()
        last = self._last_punish.get((guild_id, module, executor_id), 0.0)
        if now - last < 5.0:
            return True
        self._last_punish[(guild_id, module, executor_id)] = now
        return False

    # ============================================================== #
    # ANTI-RAID COMMANDS                                               #
    # ============================================================== #

    @hybrid_group(
        name="antiraid",
        description="Protect your server from raid/flood attacks.",
        example=",antiraid",
        with_app_command=False,
    )
    @has_permissions(manage_guild=True)
    async def antiraid(self, ctx: Context):
        if ctx.invoked_subcommand is None:
            embed = Embed(
                title="Anti-Raid",
                description=(
                    "Flood protection: mass-joins, mass-mentions, default "
                    "avatars and suspicious client fingerprints."
                ),
                color=COLORS.neutral,
            )
            embed.add_field(
                name="Subcommands",
                value=(
                    "`antiraid joins` — mass-join trigger\n"
                    "`antiraid mentions` — mass-mention trigger\n"
                    "`antiraid avatar` — default-avatar rule\n"
                    "`antiraid automation` — browser-only rule\n"
                    "`antiraid stats` — current configuration\n"
                    "`antiraid filter` — Discord AutoMod filters"
                ),
                inline=False,
            )
            await ctx.send(embed=embed)

    @staticmethod
    def _validate_amount(value: str) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            raise commands.BadArgument("`--threshold` must be an integer >= 3.")
        if n < 3:
            raise commands.BadArgument("`--threshold` must be at least 3.")
        return n

    @staticmethod
    def _validate_punishment(value: str) -> str:
        v = (value or DEFAULT_PUNISHMENT).strip().lower()
        if v not in VALID_PUNISHMENTS:
            raise commands.BadArgument(
                f"`--punishment` must be one of: {', '.join(sorted(VALID_PUNISHMENTS))}."
            )
        return v

    @antiraid.command(
        name="joins",
        description="Trigger when several members join within the same window.",
    )
    @has_permissions(manage_guild=True)
    async def antiraid_joins(
        self,
        ctx: Context,
        status: Annotated[bool, StatusConverter],
        *,
        flags: str = "",
    ) -> discord.Message:
        parsed = parse_module_flags(flags)
        config = await self.get_antiraid(ctx.guild.id)

        if not status:
            config.joins = None
            await self.save_antiraid(config)
            return await ctx.approve("Join protection has been disabled.")

        threshold = self._validate_amount(
            parsed.get("threshold") or "5"
        )
        punishment = self._validate_punishment(parsed.get("punishment") or DEFAULT_PUNISHMENT)
        config.joins = {"threshold": threshold, "punishment": punishment}
        await self.save_antiraid(config)
        return await ctx.approve(
            f"Join protection enabled (threshold `{threshold}`, punishment **{punishment}**)."
        )

    @antiraid.command(
        name="mentions",
        description="Trigger when a single message contains too many mentions.",
    )
    @has_permissions(manage_guild=True)
    async def antiraid_mentions(
        self,
        ctx: Context,
        status: Annotated[bool, StatusConverter],
        *,
        flags: str = "",
    ) -> discord.Message:
        parsed = parse_module_flags(flags)
        config = await self.get_antiraid(ctx.guild.id)

        if not status:
            config.mentions = None
            await self.save_antiraid(config)
            return await ctx.approve("Mention-spam protection has been disabled.")

        threshold = self._validate_amount(parsed.get("threshold") or "5")
        punishment = self._validate_punishment(parsed.get("punishment") or DEFAULT_PUNISHMENT)
        config.mentions = {"threshold": threshold, "punishment": punishment}
        await self.save_antiraid(config)
        return await ctx.approve(
            f"Mention-spam protection enabled (threshold `{threshold}`, punishment **{punishment}**)."
        )

    @antiraid.command(
        name="avatar",
        description="Punish accounts with default avatars.",
    )
    @has_permissions(manage_guild=True)
    async def antiraid_avatar(
        self,
        ctx: Context,
        status: Annotated[bool, StatusConverter],
        *,
        flags: str = "",
    ) -> discord.Message:
        parsed = parse_module_flags(flags)
        config = await self.get_antiraid(ctx.guild.id)

        if not status:
            config.avatar = None
            await self.save_antiraid(config)
            return await ctx.approve("Default-avatar protection has been disabled.")

        punishment = self._validate_punishment(parsed.get("punishment") or DEFAULT_PUNISHMENT)
        config.avatar = {"punishment": punishment}
        await self.save_antiraid(config)
        return await ctx.approve(f"Default-avatar protection enabled (punishment **{punishment}**).")

    @antiraid.command(
        name="automation",
        description="Punish accounts that are only online via a web client.",
    )
    @has_permissions(manage_guild=True)
    async def antiraid_automation(
        self,
        ctx: Context,
        status: Annotated[bool, StatusConverter],
        *,
        flags: str = "",
    ) -> discord.Message:
        parsed = parse_module_flags(flags)
        config = await self.get_antiraid(ctx.guild.id)

        if not status:
            config.browser = None
            await self.save_antiraid(config)
            return await ctx.approve("Automation protection has been disabled.")

        punishment = self._validate_punishment(parsed.get("punishment") or DEFAULT_PUNISHMENT)
        config.browser = {"punishment": punishment}
        await self.save_antiraid(config)
        return await ctx.approve(f"Automation protection enabled (punishment **{punishment}**).")

    @antiraid.command(
        name="stats",
        description="Show the current antiraid configuration.",
    )
    @has_permissions(manage_guild=True)
    async def antiraid_stats(self, ctx: Context) -> discord.Message:
        config = await self.get_antiraid(ctx.guild.id)
        embed = Embed(title="Anti-Raid Configuration", color=COLORS.neutral)
        embed.set_author(
            name=getattr(ctx.author, "display_name", "N/A"),
            icon_url=getattr(getattr(ctx.author, "display_avatar", None), "url", None),
        )
        embed.add_field(
            name="Joins",
            value="✅ on\nthreshold: `{}` | punishment: `{}`".format(
                config.joins["threshold"], config.joins["punishment"]
            ) if config.joins else "❌ off",
            inline=True,
        )
        embed.add_field(
            name="Mentions",
            value="✅ on\nthreshold: `{}` | punishment: `{}`".format(
                config.mentions["threshold"], config.mentions["punishment"]
            ) if config.mentions else "❌ off",
            inline=True,
        )
        embed.add_field(
            name="Avatar",
            value=f"✅ on — punishment: `{config.avatar['punishment']}`" if config.avatar else "❌ off",
            inline=True,
        )
        embed.add_field(
            name="Automation",
            value=f"✅ on — punishment: `{config.browser['punishment']}`" if config.browser else "❌ off",
            inline=True,
        )
        embed.add_field(name="Locked", value="yes" if config.locked else "no", inline=True)
        return await ctx.send(embed=embed)

    # ----- AutoMod (filter) subcommands ----- #

    @antiraid.group(
        name="filter",
        description="Manage Discord's AutoMod filters for this guild.",
    )
    @has_permissions(manage_guild=True)
    async def antiraid_filter(self, ctx: Context):
        if ctx.invoked_subcommand is None:
            embed = Embed(
                title="AutoMod filter",
                description=(
                    "`antiraid filter links <invites|external|all> <on|off> --punishment <delete|timeout>`\n"
                    "`antiraid filter exempt` — view current exemptions\n"
                    "`antiraid filter exempt-add <role|member>`\n"
                    "`antiraid filter exempt-remove <role>`\n"
                    "`antiraid filter exempt-list`"
                ),
                color=COLORS.neutral,
            )
            await ctx.send(embed=embed)

    @antiraid_filter.command(
        name="links",
        description="Enable an AutoMod rule that blocks link types.",
    )
    @has_permissions(manage_guild=True)
    async def filter_links(
        self,
        ctx: Context,
        filter_type: Literal["invites", "external", "all"],
        status: Annotated[bool, StatusConverter],
        *,
        flags: str = "",
    ) -> discord.Message:
        parsed = parse_module_flags(flags)
        punishment = (parsed.get("punishment") or "delete").lower()
        if punishment not in {"delete", "timeout"}:
            return await ctx.deny("`--punishment` must be `delete` or `timeout`.")

        try:
            if not status:
                rules = await ctx.guild.fetch_automod_rules()
                removed = 0
                for rule in rules:
                    if rule.name == f"Xrypton — {filter_type.title()} Filter":
                        await rule.delete()
                        removed += 1
                return await ctx.approve(f"Removed `{removed}` `{filter_type}` filter rule(s).")

            actions = [AutoModRuleAction(type=AutoModRuleActionType.block_message)]
            if punishment == "timeout":
                actions.append(
                    AutoModRuleAction(
                        type=AutoModRuleActionType.timeout,
                        duration=timedelta(hours=1),
                    )
                )

            trigger = AutoModTrigger(
                type=AutoModRuleTriggerType.keyword,
                regex_patterns=AUTOMOD_PATTERNS[filter_type],
            )
            exempt_roles = [
                role for role in ctx.guild.roles if role.permissions.manage_guild
            ]
            await ctx.guild.create_automod_rule(
                name=f"Xrypton — {filter_type.title()} Filter",
                event_type=AutoModRuleEventType.message_send,
                trigger=trigger,
                actions=actions,
                enabled=True,
                exempt_roles=exempt_roles,
                reason="Created via antiraid filter command",
            )
            return await ctx.approve(
                f"Created `{filter_type}` filter with punishment **{punishment}**."
            )
        except discord.Forbidden:
            return await ctx.warn("I need `manage_guild` permissions to manage AutoMod rules.")
        except discord.HTTPException as exc:
            return await ctx.warn(f"Failed to manage AutoMod rule: {exc}")

    # `antiraid filter exempt <add|remove|list>` would need a THIRD `Group`
    # object (antiraid -> filter -> exempt -> command). discord.py's hybrid
    # command machinery raises "too nested" the moment a Group is attached
    # three levels deep, regardless of `with_app_command` — that flag only
    # controls slash-sync, not whether the nesting check runs. So instead of
    # a nested `exempt` group, `exempt`, `exempt add`, `exempt remove` and
    # `exempt list` are all flat sibling *commands* directly on `filter`
    # (only two levels: antiraid -> filter -> command). Same text-command
    # UX, just spelled `,antiraid filter exempt-add ...` instead of
    # `,antiraid filter exempt add ...`.

    @antiraid_filter.command(
        name="exempt",
        aliases=["exemptions", "whitelist"],
        description="View current AutoMod filter exemptions.",
    )
    @has_permissions(manage_guild=True)
    async def filter_exempt(self, ctx: Context) -> discord.Message:
        try:
            rules = await ctx.guild.fetch_automod_rules()
        except (discord.Forbidden, discord.HTTPException) as exc:
            return await ctx.warn(f"Failed to fetch AutoMod rules: {exc}")
        exempt_roles: List[str] = []
        exempt_users: List[str] = []
        for rule in rules:
            if not rule.name.startswith("Xrypton —"):
                continue
            for role in rule.exempt_roles:
                member = ctx.guild.get_role(role.id)
                exempt_roles.append(member.mention if member else f"`{role.id}`")
            for user in rule.exempt_users:
                member = ctx.guild.get_member(user.id)
                exempt_users.append(member.mention if member else f"`{user.id}`")
        embed = Embed(title="AutoMod exemptions", color=COLORS.neutral)
        embed.add_field(
            name="Roles",
            value="\n".join(exempt_roles) if exempt_roles else "_None_",
            inline=True,
        )
        embed.add_field(
            name="Users",
            value="\n".join(exempt_users) if exempt_users else "_None_",
            inline=True,
        )
        return await ctx.send(embed=embed)

    @antiraid_filter.command(
        name="exempt-add",
        aliases=["exemptadd"],
        description="Add a role or member to the AutoMod exemption list.",
    )
    @has_permissions(manage_guild=True)
    async def filter_exempt_add(
        self, ctx: Context, target: Union[Role, discord.Member]
    ) -> discord.Message:
        try:
            rules = await ctx.guild.fetch_automod_rules()
            updated = False
            for rule in rules:
                if not rule.name.startswith("Xrypton —"):
                    continue
                if isinstance(target, Role):
                    if target not in rule.exempt_roles:
                        await rule.edit(exempt_roles=[*rule.exempt_roles, target])
                        updated = True
                else:
                    if target not in rule.exempt_users:
                        await rule.edit(exempt_users=[*rule.exempt_users, target])
                        updated = True
            if updated:
                return await ctx.approve(f"Added {target.mention} to filter exemptions.")
            return await ctx.warn("No active filter rules found for this guild.")
        except discord.Forbidden:
            return await ctx.warn("I need `manage_guild` permissions to manage AutoMod rules.")
        except discord.HTTPException as exc:
            return await ctx.warn(f"Failed to update exemptions: {exc}")

    @antiraid_filter.command(
        name="exempt-remove",
        aliases=["exemptremove"],
        description="Remove a role from the AutoMod exemption list.",
    )
    @has_permissions(manage_guild=True)
    async def filter_exempt_remove(self, ctx: Context, role: Role) -> discord.Message:
        try:
            rules = await ctx.guild.fetch_automod_rules()
            updated = False
            for rule in rules:
                if not rule.name.startswith("Xrypton —"):
                    continue
                if role.id in [r.id for r in rule.exempt_roles]:
                    new_roles = [r for r in rule.exempt_roles if r.id != role.id]
                    await rule.edit(exempt_roles=new_roles)
                    updated = True
            if updated:
                return await ctx.approve(f"Removed {role.mention} from filter exemptions.")
            return await ctx.warn("Role was not exempt from any filter.")
        except discord.Forbidden:
            return await ctx.warn("I need `manage_guild` permissions to manage AutoMod rules.")
        except discord.HTTPException as exc:
            return await ctx.warn(f"Failed to update exemptions: {exc}")

    @antiraid_filter.command(
        name="exempt-list",
        aliases=["exemptlist"],
        description="List all roles exempt from AutoMod filters.",
    )
    @has_permissions(manage_guild=True)
    async def filter_exempt_list(self, ctx: Context) -> discord.Message:
        try:
            rules = await ctx.guild.fetch_automod_rules()
            roles = set()
            for rule in rules:
                if rule.name.startswith("Xrypton —"):
                    roles.update(rule.exempt_roles)
            if not roles:
                return await ctx.warn("No roles are exempt from filters.")
            lines = []
            for role_id in sorted(roles, key=lambda r: r.name if hasattr(r, "name") else ""):
                role = ctx.guild.get_role(int(role_id)) if isinstance(role_id, int) else role_id
                if role is not None:
                    lines.append(f"• {role.mention}")
            return await ctx.approve("Exempt roles:\n" + "\n".join(lines))
        except discord.Forbidden:
            return await ctx.warn("I need `manage_guild` permissions to manage AutoMod rules.")
        except discord.HTTPException as exc:
            return await ctx.warn(f"Failed to fetch exemptions: {exc}")

    # ============================================================== #
    # ANTI-RAID LISTENERS                                              #
    # ============================================================== #

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        config = await self.get_antiraid(member.guild.id)
        if not config.enabled:
            return

        # Default avatar — punish before we even touch the join window.
        if config.avatar and self._is_default_avatar(member):
            ok = await self._do_punishment(
                member.guild,
                member,
                config.avatar["punishment"],
                reason="Default avatar detected.",
            )
            log.info(
                "default avatar %s for %s (%s) — %s",
                "punished" if ok else "FAILED",
                member, member.id, member.guild.id,
            )
            if ok:
                await self._notify(
                    member.guild, member,
                    title="Anti-Raid: Default Avatar",
                    reason="Default avatar detected.",
                    punishment=config.avatar["punishment"],
                )
            return

        # Browser-only — same idea.
        if config.browser and self._is_browser_only(member):
            ok = await self._do_punishment(
                member.guild,
                member,
                config.browser["punishment"],
                reason="Web-only gateway detected.",
            )
            log.info(
                "browser-only %s for %s (%s) — %s",
                "punished" if ok else "FAILED",
                member, member.id, member.guild.id,
            )
            if ok:
                await self._notify(
                    member.guild, member,
                    title="Anti-Raid: Browser Only",
                    reason="Spoofed gateway detected.",
                    punishment=config.browser["punishment"],
                )
            return

        # Lockdown mode — drop anyone who joins while a raid is in progress.
        if config.locked and config.joins:
            await self._do_punishment(
                member.guild,
                member,
                config.joins["punishment"],
                reason="Server is on lockdown (anti-raid active).",
            )
            return

        # Mass-join tracking.
        if config.joins:
            threshold = int(config.joins["threshold"])
            window = self._join_window.setdefault(member.guild.id, [])
            now = time.monotonic()
            window[:] = [
                (mid, ts) for (mid, ts) in window
                if now - ts < TRACK_WINDOW_SECONDS and mid in [m.id for m in member.guild.members]
            ]
            window.append((member.id, now))
            if len(window) < threshold:
                return

            punishment = config.joins["punishment"]
            offenders = self._resolve_window_members(member.guild, [w[0] for w in window])
            window.clear()

            # Activate the in-DB lockdown flag and notify owner. The reference cog
            # also called Discord's undocumented `incident-actions` endpoint;
            # that requires bot-level auth which isn't part of the public API,
            # so we keep the same UX (DM the owner + set locked=true).
            config.locked = True
            await self.save_antiraid(config)
            try:
                await self._notify_owner_raid(member.guild, offenders, punishment)
            except Exception:
                log.exception("notify_owner_raid failed")
            # Auto-unlock after 1 hour.
            self.bot.loop.create_task(self._auto_unlock(member.guild.id))

            # Now actually punish the offenders.
            for offender in offenders:
                await self._do_punishment(
                    member.guild,
                    offender,
                    punishment,
                    reason=f"Mass join detected ({len(offenders)}/{threshold}).",
                )
                await self._notify(
                    member.guild,
                    offender,
                    title="Anti-Raid: Mass Join",
                    reason=f"Detected {len(offenders)} simultaneous joins.",
                    punishment=punishment,
                )

    async def _auto_unlock(self, guild_id: int) -> None:
        with suppress(Exception):
            await self.bot.get_guild(guild_id)  # touch cache
        await self.bot.wait_until_ready()
        await discord.utils.sleep_until(datetime.now(timezone.utc) + timedelta(hours=1))
        try:
            config = await self.get_antiraid(guild_id)
            if config.locked:
                config.locked = False
                await self.save_antiraid(config)
        except Exception:
            log.exception("Failed to auto-unlock guild %s", guild_id)

    def _resolve_window_members(
        self, guild: discord.Guild, ids: Iterable[int]
    ) -> List[discord.Member]:
        out: List[discord.Member] = []
        for mid in ids:
            member = guild.get_member(int(mid))
            if member is not None:
                out.append(member)
        return out

    @staticmethod
    def _is_default_avatar(member: discord.Member) -> bool:
        avatar = member.avatar
        if avatar is None and member.guild_avatar is None:
            return True
        for asset in (avatar, member.guild_avatar):
            if asset is not None and getattr(asset, "key", None) in DEFAULT_AVATAR_HASHES:
                return True
        return False

    @staticmethod
    def _is_browser_only(member: discord.Member) -> bool:
        if member.bot or member.premium_since is not None:
            return False
        statuses = (member.desktop_status, member.mobile_status, member.web_status)
        return (
            member.web_status != discord.Status.offline
            and member.desktop_status == discord.Status.offline
            and member.mobile_status == discord.Status.offline
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return
        if message.author.guild_permissions.manage_messages:
            return

        config = await self.get_antiraid(message.guild.id)
        if not config.mentions:
            return

        threshold = int(config.mentions["threshold"])
        unique_humans = sum(
            1 for member in message.mentions
            if not member.bot and member.id != message.author.id
        )
        if unique_humans <= threshold:
            return

        punishment = config.mentions["punishment"]
        ok = await self._do_punishment(
            message.guild,
            message.author,
            punishment,
            reason=f"Mention spam ({unique_humans}/{threshold}).",
        )
        log.info(
            "mention spam %s for %s (%s) — %s",
            "punished" if ok else "FAILED",
            message.author, message.author.id, message.guild.id,
        )
        if ok:
            await self._notify(
                message.guild, message.author,
                title="Anti-Raid: Mention Spam",
                reason=f"Mentioned {unique_humans} users within one message.",
                punishment=punishment,
            )

    # ============================================================== #
    # ANTI-NUKE COMMANDS                                               #
    # ============================================================== #

    @hybrid_group(
        name="antinuke",
        aliases=["an"],
        description="Protect your server from malicious administrators.",
    )
    @has_permissions(manage_guild=True)
    async def antinuke(self, ctx: Context):
        if ctx.invoked_subcommand is None:
            embed = Embed(
                title="Anti-Nuke",
                description="Protect against malicious admins.",
                color=COLORS.neutral,
            )
            embed.add_field(
                name="Subcommands",
                value=(
                    "`antinuke settings` — view current configuration\n"
                    "`antinuke modules` — toggle ban/kick/role/channel/webhook/emoji/bot\n"
                    "`antinuke admins` — list or toggle admins\n"
                    "`antinuke whitelist` — list or toggle whitelist\n"
                    "`antinuke reset` — wipe all settings"
                ),
                inline=False,
            )
            await ctx.send(embed=embed)

    @antinuke.command(
        name="settings",
        aliases=["config"],
        description="View the antinuke configuration.",
    )
    @has_permissions(manage_guild=True)
    async def antinuke_settings(self, ctx: Context) -> discord.Message:
        settings = await self.get_settings(ctx.guild.id)
        embed = Embed(title="Anti-Nuke Settings", color=COLORS.neutral)
        embed.set_author(
            name=getattr(ctx.author, "display_name", "N/A"),
            icon_url=getattr(getattr(ctx.author, "display_avatar", None), "url", None),
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        def fmt(module: Optional[ModuleConfig]) -> str:
            if module is None:
                return "❌ off"
            return f"✅ on\nthreshold: `{module.threshold}` | punishment: **{module.punishment}**"

        embed.add_field(name="Bot add", value="✅ on" if settings.bot_enabled else "❌ off", inline=True)
        embed.add_field(name="Ban", value=fmt(settings.ban), inline=True)
        embed.add_field(name="Kick", value=fmt(settings.kick), inline=True)
        embed.add_field(name="Role", value=fmt(settings.role), inline=True)
        embed.add_field(name="Channel", value=fmt(settings.channel), inline=True)
        embed.add_field(name="Webhook", value=fmt(settings.webhook), inline=True)
        embed.add_field(name="Emoji", value=fmt(settings.emoji), inline=True)

        if settings.whitelist:
            entries = [
                self._member_label(ctx.guild, uid) for uid in settings.whitelist[:8]
            ]
            text = "\n".join(entries)
            if len(settings.whitelist) > 8:
                text += f"\n… and {len(settings.whitelist) - 8} more"
            embed.add_field(name="Whitelisted", value=text or "None", inline=False)
        else:
            embed.add_field(name="Whitelisted", value="None", inline=False)
        if settings.admins:
            entries = [
                self._member_label(ctx.guild, uid) for uid in settings.admins[:8]
            ]
            text = "\n".join(entries)
            if len(settings.admins) > 8:
                text += f"\n… and {len(settings.admins) - 8} more"
            embed.add_field(name="Admins", value=text or "None", inline=False)
        else:
            embed.add_field(name="Admins", value="None", inline=False)
        return await ctx.send(embed=embed)

    @staticmethod
    def _member_label(guild: discord.Guild, user_id: int) -> str:
        member = guild.get_member(user_id)
        if member is None:
            return f"<@{user_id}> (`{user_id}`)"
        return f"{member} (`{member.id}`)"

    @antinuke.group(
        name="modules",
        description="Toggle individual antinuke modules.",
    )
    @has_permissions(manage_guild=True)
    async def antinuke_modules(self, ctx: Context):
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    async def _toggle_module(
        self,
        ctx: Context,
        *,
        attr: str,
        label: str,
        status: bool,
    ) -> discord.Message:
        """Shared handler for every per-module command below."""
        settings = await self.get_settings(ctx.guild.id)
        if not status and getattr(settings, attr) is None:
            return await ctx.warn(f"**{label}** protection is already disabled.")
        if status and getattr(settings, attr) is not None:
            return await ctx.warn(f"**{label}** protection is already enabled.")

        if attr == "bot_enabled":
            if status:
                settings.bot_enabled = True
            else:
                settings.bot_enabled = False
        else:
            if status:
                setattr(settings, attr, ModuleConfig(threshold=5, punishment=DEFAULT_PUNISHMENT))
            else:
                setattr(settings, attr, None)
        await self.save_settings(settings)
        word = "enabled" if status else "disabled"
        return await ctx.approve(f"**{label}** protection has been **{word}**.")

    @antinuke_modules.command(
        name="bot",
        description="Prevent admins from adding bots.",
    )
    @has_permissions(manage_guild=True)
    async def antinuke_module_bot(
        self, ctx: Context, status: Annotated[bool, StatusConverter]
    ) -> discord.Message:
        return await self._toggle_module(ctx, attr="bot_enabled", label="Bot add", status=status)

    @antinuke_modules.command(
        name="ban",
        aliases=["bans"],
        description="Punish admins who mass-ban members.",
    )
    @has_permissions(manage_guild=True)
    async def antinuke_module_ban(
        self, ctx: Context, status: Annotated[bool, StatusConverter]
    ) -> discord.Message:
        return await self._toggle_module(ctx, attr="ban", label="Mass ban", status=status)

    @antinuke_modules.command(
        name="kick",
        aliases=["kicks"],
        description="Punish admins who mass-kick members.",
    )
    @has_permissions(manage_guild=True)
    async def antinuke_module_kick(
        self, ctx: Context, status: Annotated[bool, StatusConverter]
    ) -> discord.Message:
        return await self._toggle_module(ctx, attr="kick", label="Mass kick", status=status)

    @antinuke_modules.command(
        name="role",
        aliases=["roles"],
        description="Punish admins who bulk-modify roles.",
    )
    @has_permissions(manage_guild=True)
    async def antinuke_module_role(
        self, ctx: Context, status: Annotated[bool, StatusConverter]
    ) -> discord.Message:
        return await self._toggle_module(ctx, attr="role", label="Role", status=status)

    @antinuke_modules.command(
        name="channel",
        aliases=["channels"],
        description="Punish admins who bulk-modify channels.",
    )
    @has_permissions(manage_guild=True)
    async def antinuke_module_channel(
        self, ctx: Context, status: Annotated[bool, StatusConverter]
    ) -> discord.Message:
        return await self._toggle_module(ctx, attr="channel", label="Channel", status=status)

    @antinuke_modules.command(
        name="webhook",
        aliases=["webhooks", "hook", "hooks"],
        description="Punish admins who create webhooks.",
    )
    @has_permissions(manage_guild=True)
    async def antinuke_module_webhook(
        self, ctx: Context, status: Annotated[bool, StatusConverter]
    ) -> discord.Message:
        return await self._toggle_module(ctx, attr="webhook", label="Webhook", status=status)

    @antinuke_modules.command(
        name="emoji",
        aliases=["emojis", "emote", "emotes"],
        description="Punish admins who bulk-modify emojis.",
    )
    @has_permissions(manage_guild=True)
    async def antinuke_module_emoji(
        self, ctx: Context, status: Annotated[bool, StatusConverter]
    ) -> discord.Message:
        return await self._toggle_module(ctx, attr="emoji", label="Emoji", status=status)

    @antinuke.group(
        name="admins",
        description="Manage antinuke admins.",
    )
    @has_permissions(manage_guild=True)
    async def antinuke_admins(self, ctx: Context):
        if ctx.invoked_subcommand is None:
            settings = await self.get_settings(ctx.guild.id)
            if not settings.admins:
                return await ctx.warn("No antinuke admins configured.")
            entries = [
                f"`{i+1}.` {self._member_label(ctx.guild, uid)}"
                for i, uid in enumerate(settings.admins)
            ]
            embed = Embed(title="Antinuke admins", color=COLORS.neutral)
            embed.description = "\n".join(entries)
            return await ctx.paginate([embed])

    @antinuke_admins.command(
        name="add",
        description="Grant a member antinuke admin rights.",
    )
    @has_permissions(manage_guild=True)
    async def antinuke_admins_add(self, ctx: Context, member: discord.Member) -> discord.Message:
        if member.bot:
            return await ctx.warn("Bots cannot be antinuke admins.")
        if member.id == ctx.guild.owner_id:
            return await ctx.warn("The server owner already has full antinuke control.")
        settings = await self.get_settings(ctx.guild.id)
        if member.id in settings.admins:
            return await ctx.warn(f"{member.mention} is already an antinuke admin.")
        settings.admins.append(member.id)
        await self.save_settings(settings)
        return await ctx.approve(f"{member.mention} is now an antinuke admin.")

    @antinuke_admins.command(
        name="remove",
        description="Revoke a member's antinuke admin rights.",
    )
    @has_permissions(manage_guild=True)
    async def antinuke_admins_remove(self, ctx: Context, member: discord.Member) -> discord.Message:
        settings = await self.get_settings(ctx.guild.id)
        if member.id not in settings.admins:
            return await ctx.warn(f"{member.mention} is not an antinuke admin.")
        settings.admins.remove(member.id)
        await self.save_settings(settings)
        return await ctx.approve(f"{member.mention} is no longer an antinuke admin.")

    @antinuke.group(
        name="whitelist",
        aliases=["wl"],
        description="Manage antinuke whitelisted members.",
    )
    @_is_owner_or_admin()
    async def antinuke_whitelist(self, ctx: Context):
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @antinuke_whitelist.command(
        name="add",
        description="Whitelist a member from antinuke.",
    )
    @_is_owner_or_admin()
    async def antinuke_whitelist_add(
        self, ctx: Context, member: discord.Member
    ) -> discord.Message:
        settings = await self.get_settings(ctx.guild.id)
        if member.id == ctx.guild.owner_id:
            return await ctx.warn("The server owner is already exempt.")
        if member.id in settings.whitelist:
            return await ctx.warn(f"{member.mention} is already whitelisted.")
        settings.whitelist.append(member.id)
        await self.save_settings(settings)
        return await ctx.approve(f"{member.mention} is now whitelisted from antinuke.")

    @antinuke_whitelist.command(
        name="remove",
        description="Remove a member from antinuke whitelist.",
    )
    @_is_owner_or_admin()
    async def antinuke_whitelist_remove(
        self, ctx: Context, member: discord.Member
    ) -> discord.Message:
        settings = await self.get_settings(ctx.guild.id)
        if member.id not in settings.whitelist:
            return await ctx.warn(f"{member.mention} is not whitelisted.")
        settings.whitelist.remove(member.id)
        await self.save_settings(settings)
        return await ctx.approve(f"{member.mention} is no longer whitelisted.")

    @antinuke_whitelist.command(
        name="list",
        description="List whitelisted members.",
    )
    @has_permissions(manage_guild=True)
    async def antinuke_whitelist_list(self, ctx: Context) -> discord.Message:
        settings = await self.get_settings(ctx.guild.id)
        if not settings.whitelist:
            return await ctx.warn("No members are whitelisted from antinuke.")
        entries = [
            f"`{i+1}.` {self._member_label(ctx.guild, uid)}"
            for i, uid in enumerate(settings.whitelist)
        ]
        embed = Embed(title="Whitelisted members", color=COLORS.neutral)
        embed.description = "\n".join(entries)
        return await ctx.paginate([embed])

    @antinuke.command(
        name="reset",
        description="Reset every antinuke setting for this guild.",
    )
    @_is_owner_or_admin()
    async def antinuke_reset(self, ctx: Context) -> discord.Message:
        await self.bot.db.execute(
            "DELETE FROM antinuke_settings WHERE guild_id = ?",
            (ctx.guild.id,),
        )
        await self.bot.db.commit()
        return await ctx.approve("Antinuke settings have been reset.")

    # ============================================================== #
    # ANTI-NUKE LISTENERS                                              #
    # ============================================================== #

    async def _is_whitelisted(self, guild_id: int, actor: discord.Member) -> bool:
        if actor.id == guild_id:
            return True
        if actor.id == self.bot.user.id:
            return True
        settings = await self.get_settings(guild_id)
        return actor.id in (settings.whitelist + settings.admins) or actor.id == guild_id

    async def _evaluate(
        self,
        guild: discord.Guild,
        action: AuditLogAction,
        *,
        module_name: Optional[str] = None,
    ) -> None:
        """Audit-log polling helper shared by every event listener."""
        actor: Optional[discord.Member] = None
        target_id: Optional[int] = None
        try:
            async for entry in guild.audit_logs(limit=5, action=action):
                age = (discord.utils.utcnow() - entry.created_at).total_seconds()
                if age > 30:
                    continue
                if entry.user is None or entry.user.id == self.bot.user.id:
                    continue
                actor = entry.user if isinstance(entry.user, discord.Member) else None
                target_id = getattr(entry.target, "id", None)
                break
        except (discord.Forbidden, discord.HTTPException):
            return

        if actor is None:
            return
        if await self._is_whitelisted(guild.id, actor):
            return

        settings = await self.get_settings(guild.id)
        # Heuristic: pick whichever enabled module maps to this audit action.
        config: Optional[ModuleConfig]
        effective_name = module_name  # allow caller to pin the module
        if effective_name is not None:
            config = getattr(settings, effective_name, None)
        else:
            mapping = {
                AuditLogAction.bot_add: "bot_enabled",
                AuditLogAction.ban: "ban",
                AuditLogAction.kick: "kick",
                AuditLogAction.webhook_create: "webhook",
                AuditLogAction.emoji_create: "emoji",
                AuditLogAction.emoji_delete: "emoji",
                AuditLogAction.emoji_update: "emoji",
            }
            attr = mapping.get(action)
            if attr is None:
                # Role/Channel group: single shared config.
                if action in (
                    AuditLogAction.role_create,
                    AuditLogAction.role_delete,
                    AuditLogAction.role_update,
                ):
                    attr = "role"
                elif action in (
                    AuditLogAction.channel_create,
                    AuditLogAction.channel_delete,
                    AuditLogAction.channel_update,
                ):
                    attr = "channel"
            if attr is None:
                return
            if attr == "bot_enabled":
                if not settings.bot_enabled:
                    return
                self._check_and_punish(
                    guild, actor, "bot", target_id, threshold=1, punishment="ban"
                )
                return
            config = getattr(settings, attr, None)

        if config is None:
            return

        self._check_and_punish(
            guild,
            actor,
            effective_name or self._module_for_action(action),
            target_id,
            config.threshold,
            config.punishment,
        )

    @staticmethod
    def _module_for_action(action: AuditLogAction) -> str:
        return {
            AuditLogAction.ban: "ban",
            AuditLogAction.kick: "kick",
            AuditLogAction.webhook_create: "webhook",
            AuditLogAction.emoji_create: "emoji",
            AuditLogAction.emoji_delete: "emoji",
            AuditLogAction.emoji_update: "emoji",
            AuditLogAction.role_create: "role",
            AuditLogAction.role_delete: "role",
            AuditLogAction.role_update: "role",
            AuditLogAction.channel_create: "channel",
            AuditLogAction.channel_delete: "channel",
            AuditLogAction.channel_update: "channel",
        }.get(action, "unknown")

    def _check_and_punish(
        self,
        guild: discord.Guild,
        actor: discord.Member,
        module: str,
        target_id: Optional[int],
        threshold: int,
        punishment: str,
    ) -> None:
        count = self._increment(guild.id, module, actor.id)
        if count < threshold:
            return
        if self._debounced(guild.id, module, actor.id):
            return
        self._reset_counter(guild.id, module, actor.id)

        async def _runner():
            ok = await self._do_punishment(
                guild,
                actor,
                punishment,
                reason=f"Antinuke: mass {module} ({count}/{threshold}).",
                log_action=f"antinuke-{module}",
            )
            await self.log_antinuke(
                guild.id, module, actor.id, target_id, punishment, ok,
                detail=f"threshold={threshold}",
            )
            await self._notify(
                guild, actor,
                title=f"Anti-Nuke: {module.title()}",
                reason=f"Mass {module} detected.",
                punishment=punishment,
            )

        self.bot.loop.create_task(_runner())

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        await self._evaluate(guild, AuditLogAction.ban)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        # on_member_remove can also be triggered by a kick. We poll the audit
        # log to disambiguate from a voluntary leave.
        await self._evaluate(member.guild, AuditLogAction.kick)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        await self._evaluate(role.guild, AuditLogAction.role_create)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        await self._evaluate(role.guild, AuditLogAction.role_delete)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        await self._evaluate(after.guild, AuditLogAction.role_update)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        await self._evaluate(channel.guild, AuditLogAction.channel_create)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        await self._evaluate(channel.guild, AuditLogAction.channel_delete)

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ) -> None:
        await self._evaluate(after.guild, AuditLogAction.channel_update)

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.abc.GuildChannel) -> None:
        await self._evaluate(channel.guild, AuditLogAction.webhook_create)

    @commands.Cog.listener()
    async def on_guild_emojis_update(
        self,
        guild: discord.Guild,
        before: List[discord.Emoji],
        after: List[discord.Emoji],
    ) -> None:
        # We don't know whether it was a create / delete / update from the
        # event itself, so poll each action and let `_evaluate` pick the
        # most recent one within the 30s window.
        await self._evaluate(guild, AuditLogAction.emoji_create)
        await self._evaluate(guild, AuditLogAction.emoji_delete)
        await self._evaluate(guild, AuditLogAction.emoji_update)


async def setup(bot) -> None:
    """discord.py extension entry-point. The package `__init__.py` also
    imports our `setup`, but the loader walks both, so this is harmless."""
    await bot.add_cog(Security(bot))