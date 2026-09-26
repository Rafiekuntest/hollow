-- Migration V1: Initial schema
-- This represents the baseline schema from schema.sql

-- Core tables
CREATE TABLE IF NOT EXISTS guild_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER UNIQUE NOT NULL,
    prefix TEXT NOT NULL DEFAULT ','
);

CREATE TABLE IF NOT EXISTS user_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE NOT NULL,
    prefix TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    emoji_approve TEXT DEFAULT '<:approve:1547570974457733141>',
    emoji_deny TEXT DEFAULT '<:deny:1547570956191535144>',
    emoji_warn TEXT DEFAULT '<:warning:1547570970791772270>',
    emoji_cooldown TEXT DEFAULT '<:cooldown:1547572522004914218>',
    neutral_color INTEGER DEFAULT 0x2B2D31
);

INSERT OR IGNORE INTO bot_config (id, emoji_approve, emoji_deny, emoji_warn, emoji_cooldown, neutral_color)
VALUES (1, '<:approve:1547570974457733141>', '<:deny:1547570956191535144>', '<:warning:1547570970791772270>', '<:cooldown:1547572522004914218>', 0x2B2D31);

CREATE TABLE IF NOT EXISTS aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    command_name TEXT NOT NULL,
    shortcut TEXT NOT NULL,
    UNIQUE(guild_id, shortcut)
);

-- Server cog tables
CREATE TABLE IF NOT EXISTS welcome_config (
    guild_id INTEGER PRIMARY KEY,
    channels TEXT NOT NULL DEFAULT '[]',
    message TEXT,
    dm_enabled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS leave_config (
    guild_id INTEGER PRIMARY KEY,
    channels TEXT NOT NULL DEFAULT '[]',
    message TEXT
);

CREATE TABLE IF NOT EXISTS log_config (
    guild_id INTEGER PRIMARY KEY,
    category_id INTEGER,
    channels TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ignore_config (
    guild_id INTEGER PRIMARY KEY,
    users TEXT NOT NULL DEFAULT '[]',
    channels TEXT NOT NULL DEFAULT '[]',
    roles TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS fake_permission_config (
    guild_id INTEGER PRIMARY KEY,
    permissions TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS sticky_config (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER PRIMARY KEY,
    message TEXT NOT NULL,
    last_message_id INTEGER,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mod_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    moderator_id INTEGER,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admin_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    author_id INTEGER NOT NULL,
    note TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS webhook_store (
    identifier TEXT PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    webhook_id INTEGER NOT NULL,
    webhook_token TEXT NOT NULL,
    webhook_url TEXT NOT NULL,
    creator_id INTEGER NOT NULL,
    name TEXT,
    locked INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Moderation cog tables
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reactmute (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS imagemute (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS forcenick (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    forced_nick TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS lock_snapshot (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    was_locked INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS ghostping_config (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    delay INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, channel_id)
);

-- Fun cog tables
CREATE TABLE IF NOT EXISTS juul_stats (
    guild_id TEXT PRIMARY KEY,
    data TEXT,
    enabled INTEGER,
    flavor TEXT,
    holder_id TEXT,
    hits INTEGER DEFAULT 0,
    passes INTEGER DEFAULT 0,
    steals INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS juul_users (
    guild_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    hits INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

-- Automation cog tables
CREATE TABLE IF NOT EXISTS autorole_config (
    guild_id INTEGER PRIMARY KEY,
    everyone TEXT NOT NULL DEFAULT '[]',
    humans TEXT NOT NULL DEFAULT '[]',
    bots TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS autoreact_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    trigger TEXT NOT NULL,
    emoji TEXT NOT NULL,
    exclusive_channels TEXT NOT NULL DEFAULT '[]',
    exclusive_roles TEXT NOT NULL DEFAULT '[]',
    UNIQUE(guild_id, trigger)
);

CREATE TABLE IF NOT EXISTS trigger_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    trigger TEXT NOT NULL,
    response TEXT NOT NULL,
    exclusive_channels TEXT NOT NULL DEFAULT '[]',
    exclusive_roles TEXT NOT NULL DEFAULT '[]',
    UNIQUE(guild_id, trigger)
);

CREATE TABLE IF NOT EXISTS image_search_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    query TEXT NOT NULL,
    image_data TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Giveaway tables
CREATE TABLE IF NOT EXISTS giveaways (
    message_id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    host_id INTEGER NOT NULL,
    prize TEXT NOT NULL,
    winners INTEGER NOT NULL DEFAULT 1,
    duration_seconds INTEGER NOT NULL,
    minimum_age_seconds INTEGER NOT NULL DEFAULT 0,
    required_role_id INTEGER,
    entries TEXT NOT NULL DEFAULT '[]',
    ended INTEGER NOT NULL DEFAULT 0,
    winner_ids TEXT NOT NULL DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ends_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS giveaway_config (
    guild_id INTEGER PRIMARY KEY,
    default_channel_id INTEGER
);

CREATE TABLE IF NOT EXISTS saved_embeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    script TEXT NOT NULL,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS buttonroles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    button_style TEXT NOT NULL DEFAULT 'primary',
    label TEXT NOT NULL,
    emoji TEXT,
    user_limit INTEGER,
    UNIQUE(guild_id, message_id, role_id)
);

-- Engagement cog tables
CREATE TABLE IF NOT EXISTS suggestions_config (
    guild_id INTEGER PRIMARY KEY,
    channel_id INTEGER,
    panel_message_id INTEGER,
    panel_template TEXT,
    embed_template TEXT
);

CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER,
    message_id INTEGER UNIQUE,
    author_id INTEGER NOT NULL,
    suggestion TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    responder_id INTEGER,
    response TEXT,
    response_message_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS suggestions_blacklist (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS invites (
    guild_id INTEGER NOT NULL,
    code TEXT NOT NULL,
    inviter_id INTEGER,
    channel_id INTEGER,
    url TEXT,
    uses INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, code)
);

CREATE TABLE IF NOT EXISTS invite_joins (
    guild_id INTEGER NOT NULL,
    user_id INTEGER PRIMARY KEY,
    invite_code TEXT,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS levels (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    xp INTEGER NOT NULL DEFAULT 0,
    total_xp INTEGER NOT NULL DEFAULT 0,
    last_message_ts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS level_config (
    guild_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    xp_min INTEGER NOT NULL DEFAULT 15,
    xp_max INTEGER NOT NULL DEFAULT 25,
    cooldown_seconds INTEGER NOT NULL DEFAULT 60,
    level_up_message TEXT,
    level_up_channel_id INTEGER,
    announce INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS level_excludes (
    guild_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, kind, target_id)
);

CREATE TABLE IF NOT EXISTS level_rewards (
    guild_id INTEGER NOT NULL,
    level INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, level)
);

CREATE TABLE IF NOT EXISTS starboards (
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    channel_id INTEGER NOT NULL,
    emoji TEXT NOT NULL,
    threshold INTEGER NOT NULL,
    locked INTEGER NOT NULL DEFAULT 0,
    self_react INTEGER NOT NULL DEFAULT 0,
    color INTEGER,
    PRIMARY KEY (guild_id, name)
);

CREATE TABLE IF NOT EXISTS starboard_ignores (
    guild_id INTEGER NOT NULL,
    starboard_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, starboard_name, kind, target_id)
);

CREATE TABLE IF NOT EXISTS starboard_posts (
    guild_id INTEGER NOT NULL,
    starboard_name TEXT NOT NULL,
    source_message_id INTEGER NOT NULL,
    starboard_message_id INTEGER NOT NULL,
    stargazers TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (guild_id, starboard_name, source_message_id)
);

-- Giveaway extensions
CREATE TABLE IF NOT EXISTS giveaway_extra_entries (
    guild_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    entries INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, role_id)
);

CREATE TABLE IF NOT EXISTS giveaway_required_roles (
    guild_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);

CREATE TABLE IF NOT EXISTS giveaway_blacklist (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS afk_users (
    user_id INTEGER PRIMARY KEY,
    reason TEXT NOT NULL,
    since TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_autoclear TIMESTAMP
);

CREATE TABLE IF NOT EXISTS afk_ignore_channels (
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS afk_config (
    guild_id INTEGER PRIMARY KEY,
    timeout_minutes INTEGER NOT NULL DEFAULT 0,
    log_channel_id INTEGER
);

CREATE TABLE IF NOT EXISTS afk_presets (
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (user_id, name)
);

CREATE TABLE IF NOT EXISTS command_usage (
    command_name TEXT PRIMARY KEY,
    usage_count INTEGER NOT NULL DEFAULT 0
);

-- Anti-nuke cog tables
CREATE TABLE IF NOT EXISTS antinuke_config (
    guild_id INTEGER PRIMARY KEY,
    webhook_enabled INTEGER DEFAULT 0,
    webhook_threshold INTEGER DEFAULT 3,
    webhook_punishment TEXT DEFAULT 'ban',
    webhook_command INTEGER DEFAULT 0,
    channel_enabled INTEGER DEFAULT 0,
    channel_threshold INTEGER DEFAULT 3,
    channel_punishment TEXT DEFAULT 'ban',
    vanity_enabled INTEGER DEFAULT 0,
    vanity_punishment TEXT DEFAULT 'ban',
    ban_enabled INTEGER DEFAULT 0,
    ban_threshold INTEGER DEFAULT 3,
    ban_punishment TEXT DEFAULT 'ban',
    ban_command INTEGER DEFAULT 1,
    botadd_enabled INTEGER DEFAULT 0,
    emoji_enabled INTEGER DEFAULT 0,
    emoji_threshold INTEGER DEFAULT 3,
    emoji_punishment TEXT DEFAULT 'ban',
    role_enabled INTEGER DEFAULT 0,
    role_threshold INTEGER DEFAULT 3,
    role_punishment TEXT DEFAULT 'ban',
    role_command INTEGER DEFAULT 0,
    kick_enabled INTEGER DEFAULT 0,
    kick_threshold INTEGER DEFAULT 3,
    kick_punishment TEXT DEFAULT 'ban',
    kick_command INTEGER DEFAULT 1,
    permissions_enabled INTEGER DEFAULT 0,
    permissions_grant INTEGER DEFAULT 0,
    permissions_remove INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS antinuke_permissions (
    guild_id INTEGER NOT NULL,
    permission TEXT NOT NULL,
    watch_grant INTEGER NOT NULL DEFAULT 0,
    watch_remove INTEGER NOT NULL DEFAULT 0,
    threshold INTEGER NOT NULL DEFAULT 3,
    punishment TEXT NOT NULL DEFAULT 'ban',
    command_detect INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, permission)
);

CREATE TABLE IF NOT EXISTS antinuke_whitelist (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS antinuke_admins (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

-- Voicemaster cog tables
CREATE TABLE IF NOT EXISTS vm_guild (
    guild_id INTEGER PRIMARY KEY,
    default_bitrate INTEGER NOT NULL DEFAULT 64,
    default_region TEXT NOT NULL DEFAULT 'us-west',
    default_role_id INTEGER,
    default_interface INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS vm_hub (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT 'VoiceMaster',
    category_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    UNIQUE(guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS vm_temp (
    channel_id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    hub_id INTEGER,
    owner_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    locked INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    temporary INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Boost notifications
CREATE TABLE IF NOT EXISTS boost_config (
    guild_id INTEGER PRIMARY KEY,
    channel_id INTEGER,
    message TEXT
);

-- Booster roles
CREATE TABLE IF NOT EXISTS booster_role_config (
    guild_id INTEGER PRIMARY KEY,
    base_role_id INTEGER,
    enabled INTEGER NOT NULL DEFAULT 0,
    hoist INTEGER NOT NULL DEFAULT 0,
    role_limit INTEGER NOT NULL DEFAULT 1,
    share_limit INTEGER NOT NULL DEFAULT 3,
    default_color INTEGER NOT NULL DEFAULT 0x99AAB5,
    filtered_words TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS booster_roles (
    role_id INTEGER PRIMARY KEY,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS booster_role_shares (
    guild_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, role_id, recipient_id)
);

-- Confess cog
CREATE TABLE IF NOT EXISTS confess (
    guild_id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    confession INTEGER NOT NULL DEFAULT 0,
    upvote TEXT,
    downvote TEXT
);

CREATE TABLE IF NOT EXISTS confess_members (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    confession INTEGER NOT NULL,
    last_confession_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, confession)
);
CREATE INDEX IF NOT EXISTS confess_members_user_idx
    ON confess_members (guild_id, user_id);

CREATE TABLE IF NOT EXISTS confess_mute (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS confess_blacklist (
    guild_id INTEGER NOT NULL,
    word TEXT NOT NULL,
    PRIMARY KEY (guild_id, word)
);

CREATE TABLE IF NOT EXISTS confess_replies (
    message_id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    guild_id INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS confess_replies_user_idx
    ON confess_replies (guild_id, user_id);

-- Security cog (antiraid + antinuke)
CREATE TABLE IF NOT EXISTS antiraid_config (
    guild_id INTEGER PRIMARY KEY,
    joins TEXT,
    mentions TEXT,
    avatar TEXT,
    browser TEXT,
    locked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS antinuke_settings (
    guild_id INTEGER PRIMARY KEY,
    bot_enabled INTEGER NOT NULL DEFAULT 0,
    ban TEXT,
    kick TEXT,
    role TEXT,
    channel TEXT,
    webhook TEXT,
    emoji TEXT,
    whitelist TEXT NOT NULL DEFAULT '[]',
    admins TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS antinuke_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    module TEXT NOT NULL,
    executor_id INTEGER NOT NULL,
    target_id INTEGER,
    punishment TEXT NOT NULL,
    success INTEGER NOT NULL,
    detail TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS antinuke_logs_guild_idx
    ON antinuke_logs (guild_id, created_at);
CREATE INDEX IF NOT EXISTS antinuke_logs_executor_idx
    ON antinuke_logs (guild_id, executor_id, created_at);

-- Cog manager (per-guild cog enable/disable)
CREATE TABLE IF NOT EXISTS disabled_cogs (
    guild_id INTEGER NOT NULL,
    cog_name TEXT NOT NULL,
    PRIMARY KEY (guild_id, cog_name)
);