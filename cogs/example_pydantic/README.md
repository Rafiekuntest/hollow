# Example Pydantic Cog

This cog demonstrates the new **Pydantic-based configuration system** inspired by miku-framework.

## Features

- **Type-safe guild configuration** with validation
- **Type-safe user configuration** with validation
- **Automatic persistence** to JSON files
- **Hot-reload support** in development mode

## Config Models

### Guild Config (`ExampleGuildConfig`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `True` | Whether cog is enabled |
| `welcome_message` | `str` | `"Welcome to {guild}!"` | Template with `{guild}` placeholder |
| `max_warnings` | `int` | `3` | Max warnings (1-10, validated) |
| `log_channel_id` | `int \| None` | `None` | Channel for logging |
| `allowed_roles` | `list[int]` | `[]` | Role IDs for advanced features |

### User Config (`ExampleUserConfig`)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `notifications_enabled` | `bool` | `True` | User notifications |
| `preferred_color` | `int` | `0x2B2D31` | Hex color for embeds |
| `timezone` | `str` | `"UTC"` | User's timezone |

## Commands

### Guild Config (Manage Guild permission)
- `/example config` - View current config
- `/example set_welcome <message>` - Set welcome message
- `/example set_max_warnings <1-10>` - Set max warnings
- `/example set_log_channel <channel>` - Set log channel
- `/example add_allowed_role <role>` - Add allowed role
- `/example remove_allowed_role <role>` - Remove allowed role

### User Config
- `/example myconfig` - View your config
- `/example set_color <hex>` - Set preferred color
- `/example toggle_notifications` - Toggle notifications

## How It Works

1. **Models** are defined as Pydantic `BaseModel` classes
2. **Validation** happens automatically on load/save
3. **Persistence** is handled by `GuildConfigManager` / `UserConfigManager`
4. **Files** are stored in `data/config/guilds/{guild_id}.json` and `data/config/users/{user_id}.json`

## Development

In development mode (`DEV_MODE=true`):
- Hot-reload watches for file changes
- Dependencies from `pyproject.toml` are auto-installed via `uv`
- Changes to this cog trigger automatic reload