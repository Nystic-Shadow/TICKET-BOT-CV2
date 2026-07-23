"""Ticket queries and concurrency-safe ticket creation."""

from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from typing import Any, Optional

import discord

from utils.theme import PURPLE_PRIMARY

logger = logging.getLogger("discord")

_creation_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


async def is_ticket_channel(bot, channel, *, include_closed: bool = False) -> bool:
    if not channel or not hasattr(channel, "id") or not getattr(bot, "db", None):
        return False
    try:
        status_clause = "status != 'deleted'" if include_closed else "status = 'open'"
        async with bot.db.execute(
            f"SELECT 1 FROM ticket_instances WHERE channel_id = ? AND {status_clause}",
            (channel.id,),
        ) as cursor:
            return await cursor.fetchone() is not None
    except Exception:
        logger.exception("Failed to check ticket channel %s", getattr(channel, "id", None))
        return False


async def get_ticket_creator(bot, channel_id: int) -> Optional[int]:
    try:
        async with bot.db.execute(
            "SELECT creator_id FROM ticket_instances WHERE channel_id = ?",
            (channel_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else None
    except Exception:
        logger.exception("Failed to get creator for ticket channel %s", channel_id)
        return None


async def get_ticket_creator_member(bot, guild, channel_id: int):
    creator_id = await get_ticket_creator(bot, channel_id)
    if not creator_id:
        return None

    member = guild.get_member(creator_id)
    if member:
        return member

    user = bot.get_user(creator_id)
    if user:
        return user

    try:
        return await bot.fetch_user(creator_id)
    except discord.NotFound:
        logger.warning("Discord user %s no longer exists", creator_id)
    except discord.HTTPException:
        logger.exception("Failed to fetch Discord user %s", creator_id)
    return None


async def get_ticket_info(bot, channel_id: int) -> Optional[dict[str, Any]]:
    try:
        async with bot.db.execute(
            """
            SELECT creator_id, ticket_number, category, subject, description,
                   status, created_at, closed_at, claimed_by
            FROM ticket_instances
            WHERE channel_id = ?
            """,
            (channel_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return None
        keys = (
            "creator_id",
            "ticket_number",
            "category",
            "subject",
            "description",
            "status",
            "created_at",
            "closed_at",
            "claimed_by",
        )
        return dict(zip(keys, row))
    except Exception:
        logger.exception("Failed to get ticket info for channel %s", channel_id)
        return None


async def get_user_tickets(bot, guild_id: int, user_id: int) -> list[dict[str, Any]]:
    try:
        async with bot.db.execute(
            """
            SELECT channel_id, category, subject, status, ticket_number, created_at
            FROM ticket_instances
            WHERE guild_id = ? AND creator_id = ?
            ORDER BY created_at DESC
            """,
            (guild_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()
        keys = ("channel_id", "category", "subject", "status", "ticket_number", "created_at")
        return [dict(zip(keys, row)) for row in rows]
    except Exception:
        logger.exception("Failed to list tickets for user %s in guild %s", user_id, guild_id)
        return []


async def get_user_open_tickets(bot, guild_id: int, user_id: int) -> list[dict[str, Any]]:
    return [ticket for ticket in await get_user_tickets(bot, guild_id, user_id) if ticket["status"] == "open"]


async def get_guild_ticket_stats(bot, guild_id: int) -> dict[str, int]:
    try:
        async with bot.db.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END)
            FROM ticket_instances
            WHERE guild_id = ?
            """,
            (guild_id,),
        ) as cursor:
            total, open_count, closed_count = await cursor.fetchone()
        async with bot.db.execute(
            "SELECT COUNT(*) FROM ticket_categories WHERE guild_id = ?",
            (guild_id,),
        ) as cursor:
            categories = (await cursor.fetchone())[0]
        return {
            "total": total or 0,
            "open": open_count or 0,
            "closed": closed_count or 0,
            "categories": categories or 0,
        }
    except Exception:
        logger.exception("Failed to get ticket statistics for guild %s", guild_id)
        return {"total": 0, "open": 0, "closed": 0, "categories": 0}


async def get_ticket_log_channel(bot, guild_id: int):
    try:
        async with bot.db.execute(
            "SELECT log_channel_id FROM tickets WHERE guild_id = ?",
            (guild_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return bot.get_channel(row[0]) if row and row[0] else None
    except Exception:
        logger.exception("Failed to get ticket log channel for guild %s", guild_id)
        return None


def _channel_component(value: str, *, fallback: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", value.lower())
    value = re.sub(r"-+", "-", value).strip("-")
    return value[:24] or fallback


async def create_ticket_channel(
    bot,
    guild: discord.Guild,
    user: discord.Member,
    category_channel,
    category: str,
    reason: str,
) -> tuple[bool, str]:
    """Create one ticket while serializing final checks for the guild.

    Discord channel creation cannot be part of a SQLite transaction. A per-guild
    lock closes the in-process race window, and a unique database index protects
    ticket numbers if another process is accidentally started.
    """
    from utils.database import ensure_database_connection, get_additional_support_roles
    from utils.helpers import check_rate_limit

    if not await ensure_database_connection(bot):
        return False, "Database connection failed. Please try again later."

    async with _creation_locks[guild.id]:
        async with bot.db.cursor() as cursor:
            await cursor.execute(
                """
                SELECT category_id, role_id, log_channel_id, ticket_limit, maintenance_mode
                FROM tickets
                WHERE guild_id = ?
                """,
                (guild.id,),
            )
            config_row = await cursor.fetchone()
            if not config_row:
                return False, "The ticket system is not configured."

            category_id, role_id, log_channel_id, ticket_limit, maintenance = config_row
            if maintenance:
                return False, "The ticket system is currently under maintenance."

            await cursor.execute(
                "SELECT 1 FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?",
                (guild.id, user.id),
            )
            if await cursor.fetchone():
                return False, "You are blacklisted from creating tickets in this server."

            if await check_rate_limit(bot, guild.id, user.id):
                return False, "You're creating tickets too quickly. Please wait 60 seconds."

            await cursor.execute(
                """
                SELECT COUNT(*) FROM ticket_instances
                WHERE guild_id = ? AND creator_id = ? AND status = 'open'
                """,
                (guild.id, user.id),
            )
            open_count = (await cursor.fetchone())[0]
            effective_limit = ticket_limit or 3
            if open_count >= effective_limit:
                return False, (f"You have reached the maximum ticket limit ({open_count}/{effective_limit}).")

            await cursor.execute(
                "SELECT COALESCE(MAX(ticket_number), 0) + 1 FROM ticket_instances WHERE guild_id = ?",
                (guild.id,),
            )
            ticket_number = (await cursor.fetchone())[0]

        configured_category = guild.get_channel(category_id) if category_id else None
        target_category = category_channel or configured_category
        support_role = guild.get_role(role_id) if role_id else None
        additional_roles = await get_additional_support_roles(bot, guild.id)

        username = _channel_component(user.display_name, fallback="user")
        category_name = _channel_component(category, fallback="support")
        channel_name = f"{username}-{category_name}-{ticket_number:04d}"[:100]

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            ),
        }
        support_roles = [role for role in [support_role, *additional_roles] if role]
        for role in support_roles:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
            )

        try:
            channel = await guild.create_text_channel(
                channel_name,
                category=target_category,
                overwrites=overwrites,
                topic=(f"Ticket #{ticket_number:04d} for user {user.id}: {category}")[:1024],
                reason=f"Ticket created by Discord user {user.id}",
            )
        except discord.Forbidden:
            return False, "I do not have permission to create ticket channels."
        except discord.HTTPException as exc:
            logger.exception("Discord rejected ticket channel creation")
            return False, f"Discord could not create the ticket channel: {exc}"

        try:
            async with bot.db.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO ticket_instances
                    (guild_id, channel_id, creator_id, ticket_number, category,
                     subject, description, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
                    """,
                    (
                        guild.id,
                        channel.id,
                        user.id,
                        ticket_number,
                        category,
                        reason,
                        reason,
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO rate_limits (guild_id, user_id, last_ticket_time)
                    VALUES (?, ?, unixepoch('now'))
                    ON CONFLICT(guild_id, user_id)
                    DO UPDATE SET last_ticket_time = excluded.last_ticket_time
                    """,
                    (guild.id, user.id),
                )
                await bot.db.commit()
        except Exception:
            await bot.db.rollback()
            logger.exception("Ticket database insert failed; removing channel %s", channel.id)
            try:
                await channel.delete(reason="Rolling back failed ticket creation")
            except discord.HTTPException:
                logger.exception("Could not remove rolled-back ticket channel %s", channel.id)
            return False, "The ticket could not be saved. Please try again."

        allowed_role_mentions = discord.AllowedMentions(
            everyone=False,
            users=False,
            roles=support_roles,
            replied_user=False,
        )
        if support_roles:
            try:
                await channel.send(
                    " ".join(role.mention for role in support_roles),
                    allowed_mentions=allowed_role_mentions,
                )
            except discord.HTTPException:
                logger.exception("Failed to notify support roles for ticket %s", channel.id)

        try:
            from views.ticket_views import TicketControlLayout

            ticket_data = {
                "channel_id": channel.id,
                "creator_id": user.id,
                "ticket_number": ticket_number,
                "category": category,
                "reason": reason,
            }
            control_message = await channel.send(
                view=TicketControlLayout(bot, ticket_data, user),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            try:
                await control_message.pin(reason="Ticket controls")
            except discord.HTTPException:
                logger.warning("Could not pin controls in ticket channel %s", channel.id)
        except Exception:
            logger.exception("Failed to send controls in ticket channel %s", channel.id)

        webhook = None
        try:
            webhook = await channel.create_webhook(name="Ticket submission")
            await webhook.send(
                content=reason,
                username=user.display_name,
                avatar_url=user.display_avatar.url,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.warning("Webhook submission failed in ticket %s; using a normal message", channel.id)
            try:
                await channel.send(
                    f"**{discord.utils.escape_markdown(user.display_name)}**\n{reason}",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.HTTPException:
                logger.exception("Failed to send ticket submission in channel %s", channel.id)
        finally:
            if webhook:
                try:
                    await webhook.delete(reason="One-time ticket submission webhook")
                except discord.HTTPException:
                    logger.warning("Could not delete one-time webhook in ticket %s", channel.id)

        log_channel = guild.get_channel(log_channel_id) if log_channel_id else None
        if log_channel:
            try:
                from discord import ui

                log_layout = ui.LayoutView()
                log_container = ui.Container(accent_color=PURPLE_PRIMARY)
                log_container.add_item(ui.TextDisplay("### New Ticket Created"))
                log_container.add_item(ui.Separator())
                log_container.add_item(
                    ui.TextDisplay(
                        f"Ticket `#{ticket_number:04d}` created "
                        f"{discord.utils.format_dt(discord.utils.utcnow(), 'R')}\n\n"
                        f"**Channel:** {channel.mention} ({channel.id})\n"
                        f"**Creator:** {user.display_name} ({user.id})\n"
                        f"**Category:** {category}\n"
                        f"**Reason:** {reason}"
                    )
                )
                log_layout.add_item(log_container)
                await log_channel.send(
                    view=log_layout,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception:
                logger.exception("Failed to log creation of ticket %s", channel.id)

        return True, f"Ticket #{ticket_number:04d} created in {channel.mention}"


async def get_user_open_ticket_count(bot, guild_id: int, user_id: int) -> int:
    try:
        async with bot.db.execute(
            """
            SELECT COUNT(*) FROM ticket_instances
            WHERE guild_id = ? AND creator_id = ? AND status = 'open'
            """,
            (guild_id, user_id),
        ) as cursor:
            return (await cursor.fetchone())[0]
    except Exception:
        logger.exception("Failed to count tickets for user %s in guild %s", user_id, guild_id)
        return 0


async def get_ticket_limit(bot, guild_id: int) -> int:
    try:
        async with bot.db.execute(
            "SELECT ticket_limit FROM tickets WHERE guild_id = ?",
            (guild_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row and row[0] else 3
    except Exception:
        logger.exception("Failed to get ticket limit for guild %s", guild_id)
        return 3


async def check_database_connection(bot) -> bool:
    from utils.database import check_database_connection as check

    return await check(bot)
