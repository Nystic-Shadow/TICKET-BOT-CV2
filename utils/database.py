import logging
import aiosqlite
from typing import Optional, List, Tuple
import discord

from utils.config import config
from utils.theme import PURPLE_PRIMARY

logger = logging.getLogger("discord")

TICKET_CONFIG_COLUMNS = {
    "channel_id",
    "role_id",
    "category_id",
    "log_channel_id",
    "ping_role_id",
    "embed_title",
    "embed_description",
    "embed_color",
    "embed_image_url",
    "embed_footer",
    "panel_type",
    "ticket_limit",
    "maintenance_mode",
}

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS tickets (
    guild_id INTEGER PRIMARY KEY,
    channel_id INTEGER,
    role_id INTEGER,
    category_id INTEGER,
    log_channel_id INTEGER,
    ping_role_id INTEGER,
    embed_title TEXT DEFAULT 'Support Center',
    embed_description TEXT DEFAULT 'Select a category below to create a support ticket.',
    embed_color INTEGER DEFAULT {PURPLE_PRIMARY},
    embed_image_url TEXT,
    embed_footer TEXT DEFAULT 'Developed by Nystic Shadow',
    panel_type TEXT DEFAULT 'dropdown',
    ticket_limit INTEGER DEFAULT 3,
    maintenance_mode INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ticket_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    category_name TEXT NOT NULL,
    emoji TEXT,
    UNIQUE(guild_id, category_name),
    FOREIGN KEY (guild_id) REFERENCES tickets (guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ticket_instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER UNIQUE,
    creator_id INTEGER NOT NULL,
    ticket_number INTEGER NOT NULL,
    category TEXT,
    subject TEXT,
    description TEXT,
    status TEXT DEFAULT 'open',
    claimed_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    deleted_at TIMESTAMP,
    FOREIGN KEY (guild_id) REFERENCES tickets (guild_id)
);

CREATE TABLE IF NOT EXISTS ticket_user_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    ticket_number INTEGER NOT NULL,
    was_member_at_creation INTEGER DEFAULT 1,
    display_name_at_creation TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(guild_id, user_id, ticket_number)
);

CREATE TABLE IF NOT EXISTS ticket_panels (
    guild_id INTEGER PRIMARY KEY,
    channel_id INTEGER,
    message_id INTEGER,
    FOREIGN KEY (guild_id) REFERENCES tickets (guild_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ticket_blacklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    blacklisted_by INTEGER NOT NULL,
    blacklisted_at TEXT NOT NULL,
    UNIQUE(guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS additional_support_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    UNIQUE(guild_id, role_id)
);

CREATE TABLE IF NOT EXISTS rate_limits (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    last_ticket_time REAL NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS ticket_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    creator_id INTEGER NOT NULL,
    message TEXT NOT NULL,
    due_at TEXT NOT NULL,
    created_by INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
"""


async def _table_columns(db, table_name: str) -> set[str]:
    async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
        return {row[1] for row in await cursor.fetchall()}


async def _ensure_columns(db, table_name: str, columns: dict[str, str]) -> None:
    existing = await _table_columns(db, table_name)
    for name, definition in columns.items():
        if name not in existing:
            await db.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")
            logger.info("Added database column %s.%s", table_name, name)


async def _migrate_rate_limits(db) -> None:
    async with db.execute("PRAGMA table_info(rate_limits)") as cursor:
        info = await cursor.fetchall()
    primary_key = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]]
    if primary_key == ["guild_id", "user_id"]:
        return

    existing = {row[1] for row in info}
    await db.execute("DROP TABLE IF EXISTS rate_limits_legacy")
    await db.execute("ALTER TABLE rate_limits RENAME TO rate_limits_legacy")
    await db.execute(
        """
        CREATE TABLE rate_limits (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            last_ticket_time REAL NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        )
        """
    )
    if {"user_id", "last_ticket_time"}.issubset(existing):
        guild_expression = "COALESCE(guild_id, 0)" if "guild_id" in existing else "0"
        await db.execute(
            f"""
            INSERT OR REPLACE INTO rate_limits (guild_id, user_id, last_ticket_time)
            SELECT {guild_expression}, user_id, last_ticket_time
            FROM rate_limits_legacy
            """
        )
    await db.execute("DROP TABLE rate_limits_legacy")
    logger.info("Migrated rate limits to per-server keys")


async def initialize_database(bot) -> None:
    """Open the primary database and apply the canonical, idempotent schema."""
    if not getattr(bot, "db", None):
        bot.db = await aiosqlite.connect(config.DATABASE_PATH)

    await bot.db.execute("PRAGMA foreign_keys = ON")
    await bot.db.execute("PRAGMA journal_mode = WAL")
    await bot.db.execute("PRAGMA busy_timeout = 5000")
    await bot.db.executescript(SCHEMA)
    await _ensure_columns(
        bot.db,
        "tickets",
        {
            "embed_footer": "TEXT DEFAULT 'Developed by Nystic Shadow'",
            "embed_image_url": "TEXT",
            "maintenance_mode": "INTEGER DEFAULT 0",
            "panel_type": "TEXT DEFAULT 'dropdown'",
            "ticket_limit": "INTEGER DEFAULT 3",
        },
    )
    await _ensure_columns(
        bot.db,
        "ticket_instances",
        {
            "subject": "TEXT",
            "description": "TEXT",
            "claimed_by": "INTEGER",
            "deleted_at": "TIMESTAMP",
        },
    )
    await _ensure_columns(bot.db, "ticket_categories", {"emoji": "TEXT"})
    await _migrate_rate_limits(bot.db)

    # Upgrade only historical built-in defaults; preserve colors deliberately
    # customized by individual servers.
    legacy_default_colors = (
        54527,
        53247,
        0x5865F2,
        0x2F3136,
        "#00D4FF",
        "0x00D4FF",
        "00D4FF",
        "#5865F2",
        "0x5865F2",
        "5865F2",
    )
    placeholders = ", ".join("?" for _ in legacy_default_colors)
    await bot.db.execute(
        f"UPDATE tickets SET embed_color = ? WHERE embed_color IN ({placeholders})",
        (PURPLE_PRIMARY, *legacy_default_colors),
    )

    await bot.db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ticket_instances_creator ON ticket_instances(guild_id, creator_id, status)"
    )
    await bot.db.execute("CREATE INDEX IF NOT EXISTS idx_ticket_instances_status ON ticket_instances(guild_id, status)")
    await bot.db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ticket_reminders_due ON ticket_reminders(delivered_at, due_at)"
    )
    try:
        await bot.db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_ticket_number_unique ON ticket_instances(guild_id, ticket_number)"
        )
    except aiosqlite.IntegrityError:
        logger.error("Duplicate ticket numbers exist; unique ticket-number protection could not be enabled")

    await bot.db.commit()
    logger.info("Primary database initialized at %s", config.DATABASE_PATH)


async def check_database_connection(bot) -> bool:
    try:
        if not hasattr(bot, "db") or bot.db is None:
            logger.error("Bot database object is None")
            return False

        async with bot.db.cursor() as cur:
            await cur.execute("SELECT 1")
            await cur.fetchone()
            return True
    except Exception as e:
        logger.error(f"Database connection check failed: {e}")
        return False


async def ensure_database_connection(bot):
    """Ensure database connection is valid, attempt reconnection if needed"""
    if not await check_database_connection(bot):
        try:
            if hasattr(bot, "db") and bot.db:
                await bot.db.close()
            bot.db = None
            await initialize_database(bot)
            logger.info("Database reconnected successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to reconnect to database: {e}")
            return False
    return True


async def get_ticket_channel(bot, guild_id: int) -> Optional[discord.TextChannel]:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute("SELECT channel_id FROM tickets WHERE guild_id = ?", (guild_id,))
            result = await cur.fetchone()
            if result and result[0]:
                guild = bot.get_guild(guild_id)
                return guild.get_channel(result[0]) if guild else None
    except Exception as e:
        logger.error(f"Error getting ticket channel: {e}")
        return None


async def get_ticket_role(bot, guild_id: int) -> Optional[discord.Role]:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute("SELECT role_id FROM tickets WHERE guild_id = ?", (guild_id,))
            result = await cur.fetchone()
            if result and result[0]:
                guild = bot.get_guild(guild_id)
                return guild.get_role(result[0]) if guild else None
    except Exception as e:
        logger.error(f"Error getting ticket role: {e}")
        return None


async def get_ticket_category(bot, guild_id: int) -> Optional[discord.CategoryChannel]:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute("SELECT category_id FROM tickets WHERE guild_id = ?", (guild_id,))
            result = await cur.fetchone()
            if result and result[0]:
                guild = bot.get_guild(guild_id)
                return guild.get_channel(result[0]) if guild else None
    except Exception as e:
        logger.error(f"Error getting ticket category: {e}")
        return None


async def get_ticket_log_channel(bot, guild_id: int) -> Optional[discord.TextChannel]:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute("SELECT log_channel_id FROM tickets WHERE guild_id = ?", (guild_id,))
            result = await cur.fetchone()
            if result and result[0]:
                guild = bot.get_guild(guild_id)
                return guild.get_channel(result[0]) if guild else None
    except Exception as e:
        logger.error(f"Error getting ticket log channel: {e}")
        return None


async def get_ping_role(bot, guild_id: int) -> Optional[discord.Role]:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute("SELECT ping_role_id FROM tickets WHERE guild_id = ?", (guild_id,))
            result = await cur.fetchone()
            if result and result[0]:
                guild = bot.get_guild(guild_id)
                return guild.get_role(result[0]) if guild else None
    except Exception as e:
        logger.error(f"Error getting ping role: {e}")
        return None


async def get_additional_support_roles(bot, guild_id: int) -> List[discord.Role]:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute("SELECT role_id FROM additional_support_roles WHERE guild_id = ?", (guild_id,))
            results = await cur.fetchall()
            guild = bot.get_guild(guild_id)
            if not guild:
                return []
            roles = []
            for row in results:
                role = guild.get_role(row[0])
                if role:
                    roles.append(role)
            return roles
    except Exception as e:
        logger.error(f"Error getting additional support roles: {e}")
        return []


async def add_support_role(bot, guild_id: int, role_id: int) -> Tuple[bool, str]:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM additional_support_roles WHERE guild_id = ? AND role_id = ?", (guild_id, role_id)
            )
            if await cur.fetchone():
                return False, "This role is already added as a support role."
            await cur.execute(
                "INSERT INTO additional_support_roles (guild_id, role_id) VALUES (?, ?)", (guild_id, role_id)
            )
            await bot.db.commit()
            return True, "Support role added successfully."
    except Exception as e:
        logger.error(f"Error adding support role: {e}")
        return False, f"Failed to add support role: {str(e)}"


async def remove_support_role(bot, guild_id: int, role_id: int) -> Tuple[bool, str]:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute(
                "DELETE FROM additional_support_roles WHERE guild_id = ? AND role_id = ?", (guild_id, role_id)
            )
            if cur.rowcount == 0:
                return False, "This role is not in the support roles list."
            await bot.db.commit()
            return True, "Support role removed successfully."
    except Exception as e:
        logger.error(f"Error removing support role: {e}")
        return False, f"Failed to remove support role: {str(e)}"


async def get_all_support_roles(bot, guild_id: int) -> List[discord.Role]:
    try:
        main_role = await get_ticket_role(bot, guild_id)
        additional_roles = await get_additional_support_roles(bot, guild_id)
        all_roles = []
        if main_role:
            all_roles.append(main_role)
        all_roles.extend(additional_roles)
        return all_roles
    except Exception as e:
        logger.error(f"Error getting all support roles: {e}")
        return []


async def get_ticket_categories(bot, guild_id: int) -> List[Tuple[str, str]]:
    try:
        if not bot.db:
            return []
        async with bot.db.cursor() as cur:
            await cur.execute(
                "SELECT category_name, emoji FROM ticket_categories WHERE guild_id = ? ORDER BY category_name",
                (guild_id,),
            )
            results = await cur.fetchall()
            return [(row[0], row[1]) for row in results]
    except Exception as e:
        logger.error(f"Error getting ticket categories: {e}")
        return []


async def get_ticket_categories_with_emojis(bot, guild_id: int) -> List[Tuple[str, str]]:
    try:
        if not bot.db:
            return []
        async with bot.db.cursor() as cur:
            await cur.execute(
                "SELECT category_name, emoji FROM ticket_categories WHERE guild_id = ? ORDER BY category_name",
                (guild_id,),
            )
            results = await cur.fetchall()
            return [(row[0], row[1]) for row in results]
    except Exception as e:
        logger.error(f"Error getting ticket categories with emojis: {e}")
        return []


async def add_ticket_category(bot, guild_id: int, category_name: str, emoji: str = None) -> Tuple[bool, str]:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM ticket_categories WHERE guild_id = ? AND category_name = ?", (guild_id, category_name)
            )
            if await cur.fetchone():
                return False, f"Category '{category_name}' already exists."

            await cur.execute("SELECT COUNT(*) FROM ticket_categories WHERE guild_id = ?", (guild_id,))
            count = (await cur.fetchone())[0]
            if count >= 25:
                return False, "Maximum of 25 categories allowed per server."

            await cur.execute(
                "INSERT INTO ticket_categories (guild_id, category_name, emoji) VALUES (?, ?, ?)",
                (guild_id, category_name, emoji),
            )
            await bot.db.commit()

            emoji_display = f" with emoji {emoji}" if emoji else ""
            return True, f"Category '{category_name}'{emoji_display} has been added successfully."
    except Exception as e:
        logger.error(f"Error adding ticket category: {e}")
        return False, f"Database error: {str(e)}"


async def remove_ticket_category(bot, guild_id: int, category_name: str) -> Tuple[bool, str]:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute(
                "DELETE FROM ticket_categories WHERE guild_id = ? AND category_name = ?", (guild_id, category_name)
            )
            if cur.rowcount == 0:
                return False, f"Category '{category_name}' not found."

            await bot.db.commit()
            return True, f"Category '{category_name}' has been removed successfully."
    except Exception as e:
        logger.error(f"Error removing ticket category: {e}")
        return False, f"Database error: {str(e)}"


async def reset_ticket_categories(bot, guild_id: int) -> Tuple[bool, str]:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute("DELETE FROM ticket_categories WHERE guild_id = ?", (guild_id,))
            count = cur.rowcount
            await bot.db.commit()

            if count == 0:
                return False, "No categories found to reset."

            return True, f"All {count} categories have been reset successfully."
    except Exception as e:
        logger.error(f"Error resetting ticket categories: {e}")
        return False, f"Database error: {str(e)}"


async def user_has_support_role(bot, user: discord.Member) -> bool:
    try:
        if not user or not user.guild:
            return False

        if user.guild_permissions.administrator:
            return True

        async with bot.db.cursor() as cur:
            await cur.execute("SELECT role_id FROM tickets WHERE guild_id = ?", (user.guild.id,))
            result = await cur.fetchone()

            if result and result[0]:
                primary_support_role = user.guild.get_role(result[0])
                if primary_support_role and primary_support_role in user.roles:
                    return True

            await cur.execute("SELECT role_id FROM additional_support_roles WHERE guild_id = ?", (user.guild.id,))
            additional_roles = await cur.fetchall()

            for role_row in additional_roles:
                role_id = role_row[0]
                additional_role = user.guild.get_role(role_id)
                if additional_role and additional_role in user.roles:
                    return True

            return False
    except Exception as e:
        logger.error(f"Error checking support roles for user {user.id} in guild {user.guild.id}: {e}")
        return False


async def user_has_any_support_role(bot, user):
    """Check if user has any support role (primary or additional)"""
    try:
        if not user or not hasattr(user, "guild"):
            return False

        async with bot.db.cursor() as cur:
            await cur.execute("SELECT role_id FROM tickets WHERE guild_id = ?", (user.guild.id,))
            result = await cur.fetchone()

            if result and result[0]:
                primary_support_role = user.guild.get_role(result[0])
                if primary_support_role and primary_support_role in user.roles:
                    return True

            await cur.execute("SELECT role_id FROM additional_support_roles WHERE guild_id = ?", (user.guild.id,))
            additional_roles = await cur.fetchall()

            for role_row in additional_roles:
                additional_role = user.guild.get_role(role_row[0])
                if additional_role and additional_role in user.roles:
                    return True

            return False
    except Exception as e:
        logger.error(f"Error checking support roles: {e}")
        return False


async def get_user_open_tickets(bot, guild_id: int, user_id: int) -> int:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute(
                """
                SELECT COUNT(*) FROM ticket_instances
                WHERE guild_id = ? AND creator_id = ? AND status = 'open'
            """,
                (guild_id, user_id),
            )
            result = await cur.fetchone()
            return result[0] if result else 0
    except Exception as e:
        logger.error(f"Error getting user open tickets: {e}")
        return 0


async def check_user_ticket_limit(bot, guild_id: int, user_id: int) -> Tuple[bool, int, int]:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute("SELECT ticket_limit FROM tickets WHERE guild_id = ?", (guild_id,))
            result = await cur.fetchone()
            limit = result[0] if result else 3

            await cur.execute(
                "SELECT COUNT(*) FROM ticket_instances WHERE guild_id = ? AND creator_id = ? AND status = 'open'",
                (guild_id, user_id),
            )
            count = (await cur.fetchone())[0]

            can_create = count < limit
            return can_create, count, limit
    except Exception as e:
        logger.error(f"Error checking user ticket limit: {e}")
        return True, 0, 3


async def get_user_safe_mention(bot, user_id: int, guild_id: int = None) -> str:
    try:
        if guild_id:
            guild = bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(user_id)
                if member:
                    return member.mention

        user = bot.get_user(user_id)
        if user:
            return user.mention

        return f"<@{user_id}>"
    except Exception as e:
        logger.error(f"Error getting user mention for {user_id}: {e}")
        return f"<@{user_id}>"


async def get_user_safe_display_name(bot, user_id: int, guild_id: int = None) -> str:
    try:
        if guild_id:
            guild = bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(user_id)
                if member:
                    return member.display_name

        user = bot.get_user(user_id)
        if user:
            return user.display_name

        return "Unknown User"
    except Exception as e:
        logger.error(f"Error getting user display name for {user_id}: {e}")
        return "Unknown User"


def convert_color_to_int(color_value):
    if color_value is None:
        return PURPLE_PRIMARY
    if isinstance(color_value, int):
        return color_value
    if isinstance(color_value, str):
        try:
            color_str = color_value.strip()
            if color_str.startswith("#"):
                return int(color_str[1:], 16)
            elif color_str.startswith("0x"):
                return int(color_str, 16)
            else:
                return int(color_str, 16)
        except (ValueError, AttributeError):
            return PURPLE_PRIMARY
    return PURPLE_PRIMARY


async def add_or_update_ticket_config(bot, guild_id: int, **kwargs) -> bool:
    try:
        unknown = set(kwargs) - TICKET_CONFIG_COLUMNS
        if unknown:
            raise ValueError(f"Unsupported ticket configuration fields: {sorted(unknown)}")
        if not kwargs:
            return True

        if "embed_color" in kwargs:
            kwargs["embed_color"] = convert_color_to_int(kwargs["embed_color"])

        async with bot.db.cursor() as cur:
            update_keys = list(kwargs)
            insert_values = dict(kwargs)
            insert_values.setdefault("embed_footer", config.DEFAULT_PANEL_FOOTER)
            insert_keys = list(insert_values)
            placeholders = ", ".join("?" for _ in range(len(insert_keys) + 1))
            updates = ", ".join(f"{key} = excluded.{key}" for key in update_keys)
            query = (
                f"INSERT INTO tickets (guild_id, {', '.join(insert_keys)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT(guild_id) DO UPDATE SET {updates}"
            )
            await cur.execute(
                query,
                [guild_id, *(insert_values[key] for key in insert_keys)],
            )

            await bot.db.commit()
            return True
    except Exception as e:
        logger.error(f"Error updating ticket config: {e}")
        return False


async def get_ticket_limit(bot, guild_id: int) -> int:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute("SELECT ticket_limit FROM tickets WHERE guild_id = ?", (guild_id,))
            result = await cur.fetchone()
            return result[0] if result and result[0] else 3
    except Exception as e:
        logger.error(f"Error getting ticket limit: {e}")
        return 3


async def is_user_blacklisted(bot, guild_id: int, user_id: int) -> bool:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute("SELECT 1 FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            return await cur.fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking user blacklist status: {e}")
        return False


async def migrate_database(bot):
    """Backward-compatible entry point for older callers."""
    await initialize_database(bot)
