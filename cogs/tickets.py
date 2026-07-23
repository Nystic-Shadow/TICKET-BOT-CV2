import discord
from discord import app_commands, ui
from discord.ext import commands, tasks
import logging
import re
from datetime import timedelta
from typing import Optional
from utils.helpers import utc_to_gmt, send_ephemeral_container, send_container, send_channel_container
from utils.database import (
    check_database_connection,
    get_ticket_role,
    get_ticket_categories,
    user_has_support_role,
    add_ticket_category,
    remove_ticket_category,
    reset_ticket_categories,
)
from utils.tickets import is_ticket_channel, get_ticket_creator
from utils.theme import PURPLE_PRIMARY
from views.ticket_views import TicketSetupLayout, TicketPanelLayoutView, TicketButtonPanelLayoutView


logger = logging.getLogger("discord")


async def update_ticket_panel(bot, guild_id: int, panel_type: str = None) -> tuple[bool, str]:
    try:
        if not await check_database_connection(bot):
            return False, "Database connection failed. Please try again later."

        async with bot.db.cursor() as cur:
            await cur.execute(
                "SELECT channel_id, embed_title, embed_description, embed_color, embed_image_url, panel_type FROM tickets WHERE guild_id = ?",
                (guild_id,),
            )
            result = await cur.fetchone()
            if not result:
                return False, "Support system is not set up. Use `/setup` first."

            channel_id, embed_title, embed_description, embed_color, embed_image_url, current_panel_type = result
            channel = bot.get_channel(channel_id)
            if not channel:
                return False, "Support channel not found. Please set up the support system again."

            panel_type = panel_type or current_panel_type
            if panel_type not in ("dropdown", "button"):
                return False, "Invalid panel type. Use `dropdown` or `button`."

            categories = await get_ticket_categories(bot, guild_id)
            if not categories:
                return (
                    False,
                    "No ticket categories found. You must create categories first using `/category add <name>` before sending a panel.",
                )

            def convert_color_to_accent(color_value):
                if color_value is None:
                    return None
                if isinstance(color_value, int):
                    if color_value == -1:
                        return None
                    return color_value
                if isinstance(color_value, str):
                    color_str = color_value.strip().lower()
                    if color_str == "none" or color_str == "":
                        return None
                    try:
                        if color_str.startswith("#"):
                            return int(color_str[1:], 16)
                        elif color_str.startswith("0x"):
                            return int(color_str, 16)
                        else:
                            return int(color_str, 16)
                    except (ValueError, AttributeError):
                        return PURPLE_PRIMARY
                return PURPLE_PRIMARY

            accent_color = convert_color_to_accent(embed_color)

            guild = bot.get_guild(guild_id)
            server_icon_url = guild.icon.url if guild and guild.icon else None

            try:
                async with bot.db.cursor() as cur:
                    await cur.execute("SELECT message_id FROM ticket_panels WHERE guild_id = ?", (guild_id,))
                    old_message = await cur.fetchone()
                    if old_message:
                        try:
                            message = await channel.fetch_message(old_message[0])
                            await message.delete()
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            logger.warning(f"Could not delete old panel message {old_message[0]} in guild {guild_id}")
                            pass

                if panel_type == "dropdown":
                    view = TicketPanelLayoutView(
                        bot,
                        categories,
                        guild_id,
                        embed_title,
                        embed_description,
                        accent_color,
                        embed_image_url,
                        server_icon_url,
                    )
                else:
                    view = TicketButtonPanelLayoutView(
                        bot,
                        categories,
                        guild_id,
                        embed_title,
                        embed_description,
                        accent_color,
                        embed_image_url,
                        server_icon_url,
                    )

                message = await channel.send(view=view)
                async with bot.db.cursor() as cur:
                    await cur.execute(
                        "INSERT OR REPLACE INTO ticket_panels (guild_id, channel_id, message_id) VALUES (?, ?, ?)",
                        (guild_id, channel_id, message.id),
                    )
                    await bot.db.commit()
                return True, f"Support panel has been sent to {channel.mention}."
            except discord.Forbidden as e:
                return False, f"I don't have permission to send messages in the support channel: {e}"
            except Exception as e:
                return False, f"An error occurred: {e}"
    except Exception as e:
        logger.error(f"Error updating ticket panel for guild {guild_id}: {e}")
        return False, f"Database error occurred: {str(e)}"


class SupportSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        if not hasattr(bot, "active_setups"):
            bot.active_setups = {}

    async def cog_before_invoke(self, ctx: commands.Context) -> None:
        """Acknowledge slash invocations before database or Discord I/O."""
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer(ephemeral=True)

    async def cog_load(self):
        from utils.database import initialize_database

        await initialize_database(self.bot)
        if not self.reminder_dispatcher.is_running():
            self.reminder_dispatcher.start()
        return

        try:
            async with self.bot.db.cursor() as cur:
                await cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS tickets (
                        guild_id INTEGER PRIMARY KEY,
                        channel_id INTEGER,
                        role_id INTEGER,
                        category_id INTEGER,
                        log_channel_id INTEGER,
                        ping_role_id INTEGER,
                        embed_title TEXT DEFAULT '<:Ticket_icons:1382703084815257610> Support Center',
                        embed_description TEXT DEFAULT 'Need help? Select a category below to create a support ticket. Our team will assist you shortly! <:UA_Rocket_icons:1382701592851124254>',
                        embed_color INTEGER DEFAULT {PURPLE_PRIMARY},
                        embed_image_url TEXT,
                        embed_footer TEXT DEFAULT 'Developed by Nystic Shadow',
                        panel_type TEXT DEFAULT 'dropdown',
                        ticket_limit INTEGER DEFAULT 3
                    )
                """)

                try:
                    await cur.execute(
                        "ALTER TABLE tickets ADD COLUMN embed_footer TEXT DEFAULT 'Developed by Nystic Shadow'"
                    )
                except Exception:
                    pass  # Column already exists

                try:
                    await cur.execute("ALTER TABLE tickets ADD COLUMN embed_image_url TEXT")
                except Exception:
                    pass  # Column already exists

                try:
                    await cur.execute("ALTER TABLE tickets ADD COLUMN maintenance_mode BOOLEAN DEFAULT 0")
                except Exception:
                    pass  # Column already exists

                try:
                    await cur.execute("ALTER TABLE tickets ADD COLUMN panel_type TEXT DEFAULT 'dropdown'")
                except Exception:
                    pass  # Column already exists

                try:
                    await cur.execute("ALTER TABLE tickets ADD COLUMN ticket_limit INTEGER DEFAULT 3")
                except Exception:
                    pass  # Column already exists

                try:
                    await cur.execute("ALTER TABLE ticket_instances ADD COLUMN subject TEXT")
                except Exception:
                    pass  # Column already exists

                try:
                    await cur.execute("ALTER TABLE ticket_instances ADD COLUMN description TEXT")
                except Exception:
                    pass  # Column already exists

                try:
                    await cur.execute("ALTER TABLE ticket_instances ADD COLUMN claimed_by INTEGER")
                except Exception:
                    pass  # Column already exists

                try:
                    await cur.execute("SELECT embed_color FROM tickets LIMIT 1")
                    result = await cur.fetchone()
                    if result and isinstance(result[0], str):
                        await cur.execute("SELECT guild_id, embed_color FROM tickets")
                        rows = await cur.fetchall()
                        for guild_id, color_str in rows:
                            try:
                                if color_str.startswith("#"):
                                    color_int = int(color_str[1:], 16)
                                elif color_str.startswith("0x"):
                                    color_int = int(color_str, 16)
                                else:
                                    color_int = int(color_str, 16) if color_str.isdigit() else PURPLE_PRIMARY
                            except (ValueError, AttributeError):
                                color_int = PURPLE_PRIMARY

                            await cur.execute(
                                "UPDATE tickets SET embed_color = ? WHERE guild_id = ?", (color_int, guild_id)
                            )
                        logger.info("Migrated color values from string to integer")
                except Exception:
                    pass  # Column already properly configured

                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS ticket_categories (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER,
                        category_name TEXT,
                        UNIQUE(guild_id, category_name)
                    )
                """)

                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS ticket_instances (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER,
                        channel_id INTEGER UNIQUE,
                        creator_id INTEGER,
                        ticket_number INTEGER,
                        category TEXT,
                        subject TEXT,
                        description TEXT,
                        status TEXT DEFAULT 'open',
                        claimed_by INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        closed_at TIMESTAMP
                    )
                """)

                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS ticket_user_status (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER,
                        user_id INTEGER,
                        ticket_number INTEGER,
                        was_member_at_creation BOOLEAN DEFAULT 1,
                        display_name_at_creation TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(guild_id, user_id, ticket_number)
                    )
                """)

                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS ticket_panels (
                        guild_id INTEGER PRIMARY KEY,
                        channel_id INTEGER,
                        message_id INTEGER,
                        FOREIGN KEY (guild_id) REFERENCES tickets (guild_id)
                    )
                """)

                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS rate_limits (
                        user_id INTEGER PRIMARY KEY,
                        last_ticket_time REAL
                    )
                """)

                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS ticket_blacklist (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER,
                        user_id INTEGER,
                        blacklisted_by INTEGER,
                        blacklisted_at TEXT,
                        UNIQUE(guild_id, user_id)
                    )
                """)

                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS additional_support_roles (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        guild_id INTEGER,
                        role_id INTEGER,
                        UNIQUE(guild_id, role_id)
                    )
                """)

                await self.bot.db.commit()
        except Exception as e:
            logger.error(f"Error setting up database: {e}")
            raise

    async def cog_unload(self):
        self.reminder_dispatcher.cancel()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        if message.guild.id in self.bot.active_setups:
            view = self.bot.active_setups[message.guild.id]
            if hasattr(view, "handle_custom_message"):
                await view.handle_custom_message(message)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        try:
            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT id FROM ticket_instances WHERE channel_id = ?", (channel.id,))
                result = await cur.fetchone()

                if result:
                    await cur.execute(
                        """
                        UPDATE ticket_instances
                        SET status = 'deleted', deleted_at = CURRENT_TIMESTAMP
                        WHERE channel_id = ?
                        """,
                        (channel.id,),
                    )
                    await self.bot.db.commit()
                    logger.info(
                        "Ticket channel %s (ID: %s) was deleted; history was retained",
                        channel.name,
                        channel.id,
                    )
        except Exception as e:
            logger.error(f"Error handling channel deletion for potential ticket: {e}")

    @commands.hybrid_command(name="setup", description="Set up the support ticket system for this server.")
    @commands.has_permissions(administrator=True)
    async def setup_tickets(self, ctx: commands.Context):
        invoker = ctx.author if isinstance(ctx, commands.Context) else ctx.user
        logger.info(f"Setup tickets command invoked by {invoker}")
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            if not await check_database_connection(self.bot):
                await send_ephemeral_container(
                    ctx, "<:icons_Wrong:1382701332955402341> | Database connection failed. Please try again later."
                )
                return

            view = TicketSetupLayout(self.bot, ctx)
            self.bot.active_setups[ctx.guild.id] = view

            if isinstance(ctx, discord.Interaction):
                await ctx.followup.send(view=view, ephemeral=True)
            else:
                await ctx.send(view=view, ephemeral=True)

            await view.wait()

            if ctx.guild.id in self.bot.active_setups:
                del self.bot.active_setups[ctx.guild.id]

        except Exception as e:
            logger.error(f"Error in setup_tickets: {e}")
            await send_ephemeral_container(ctx, f"<:icons_Wrong:1382701332955402341> | An error occurred: {e}")

    @commands.hybrid_group(name="category", description="Manage support categories")
    @commands.has_permissions(administrator=True)
    async def category(self, ctx: commands.Context):
        """Manage support categories"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @category.command(name="add", description="Add a new support category.")
    @app_commands.describe(
        category="The category name to add", emoji="Optional emoji for the category (default: ticket icon)"
    )
    @commands.has_permissions(administrator=True)
    async def category_add(self, ctx: commands.Context, category: str, emoji: str = None):
        logger.info(
            f"Add category command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}: {category}"
        )
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            if isinstance(ctx, commands.Context) and "|" in category and not emoji:
                parts = category.split("|", 1)
                if len(parts) == 2:
                    emoji = parts[0].strip()
                    category = parts[1].strip()
            elif isinstance(ctx, commands.Context) and not emoji:
                words = category.split()
                if len(words) > 1:
                    potential_emoji = words[0]
                    if len(potential_emoji) <= 4 and not potential_emoji.isalpha():
                        emoji = potential_emoji
                        category = " ".join(words[1:])

            if len(category) > 25:
                await send_ephemeral_container(
                    ctx, "<:icons_Wrong:1382701332955402341> | Category name cannot exceed 25 characters."
                )
                return

            category = category.title()
            success, message = await add_ticket_category(self.bot, ctx.guild.id, category, emoji)

            if success:
                try:
                    category_name = f"{category} Tickets"
                    await ctx.guild.create_category(category_name)
                    message += f" Discord category '{category_name}' created."
                except discord.Forbidden:
                    message += " (Warning: Could not create Discord category - missing permissions)"
                except Exception as e:
                    message += f" (Warning: Could not create Discord category - {str(e)})"

            title = "Success" if success else "Error"
            await send_container(ctx, title, message)

            if success:
                panel_success, panel_message = await update_ticket_panel(self.bot, ctx.guild.id)
                if not panel_success and "not set up" not in panel_message:
                    await send_ephemeral_container(ctx, f"Failed to update support panel: {panel_message}")

        except Exception as e:
            logger.error(f"Error in add_category: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @category.command(name="remove", description="Remove a support category.")
    @app_commands.describe(category="The category name to remove")
    @commands.has_permissions(administrator=True)
    async def category_remove(self, ctx: commands.Context, *, category: str):
        logger.info(
            f"Remove category command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}: {category}"
        )
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            category = category.title()
            success, message = await remove_ticket_category(self.bot, ctx.guild.id, category)

            title = "Success" if success else "Error"
            await send_container(ctx, title, message)

            if success:
                panel_success, panel_message = await update_ticket_panel(self.bot, ctx.guild.id)
                if not panel_success and "not set up" not in panel_message:
                    await send_ephemeral_container(ctx, f"Failed to update support panel: {panel_message}")

        except Exception as e:
            logger.error(f"Error in remove_category: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @category.command(name="list", description="List all support categories.")
    async def category_list(self, ctx: commands.Context):
        logger.info(
            f"List categories command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}"
        )
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            invoker = ctx.author if isinstance(ctx, commands.Context) else ctx.user
            user_has_support = await user_has_support_role(self.bot, invoker)
            is_admin = invoker.guild_permissions.administrator

            if not (user_has_support or is_admin):
                await send_container(
                    ctx,
                    "Permission Denied",
                    "You don't have permission to view the categories list.\n\nThis command is restricted to support staff and administrators only.",
                )
                return

            categories = await get_ticket_categories(self.bot, ctx.guild.id)
            if not categories:
                await send_ephemeral_container(
                    ctx, "No support categories found. Use `/category add <category>` to add some."
                )
                return

            category_list = []
            for category_name, emoji in categories:
                category_list.append(f"- {category_name}")

            await send_container(ctx, "Support Categories", "\n".join(category_list))

        except Exception as e:
            logger.error(f"Error in list_categories: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @commands.hybrid_command(name="sendpanel", description="Send or update the support panel.")
    @app_commands.describe(type="The type of panel to send")
    @app_commands.choices(
        type=[
            app_commands.Choice(name="Dropdown", value="dropdown"),
            app_commands.Choice(name="Button", value="button"),
        ]
    )
    @app_commands.default_permissions(administrator=True)
    @commands.has_permissions(administrator=True)
    async def send_panel(self, ctx: commands.Context, type: str):
        logger.info(
            f"Send panel command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}: type={type}"
        )
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            categories = await get_ticket_categories(self.bot, ctx.guild.id)
            if not categories:
                await send_container(
                    ctx,
                    "No Categories Found",
                    "You must create at least one ticket category before sending a panel.\n\nUse `/category add <name>` to create categories.\n\nExamples:\n`/category add Technical Support`\n`/category add General Help`\n`/category add Billing Issues`",
                )
                return

            success, message = await update_ticket_panel(self.bot, ctx.guild.id, panel_type=type)
            title = "Support Panel Sent" if success else "Error"
            await send_container(ctx, title, message)

            if success:
                try:
                    async with self.bot.db.cursor() as cur:
                        await cur.execute("UPDATE tickets SET panel_type = ? WHERE guild_id = ?", (type, ctx.guild.id))
                        await self.bot.db.commit()
                except Exception as e:
                    logger.error(f"Error updating panel type: {e}")

        except Exception as e:
            logger.error(f"Error in send_panel: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @commands.hybrid_command(name="close", description="Close the current support ticket.")
    async def close_ticket(self, ctx: commands.Context):
        logger.info(f"Close ticket command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}")
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            if not await is_ticket_channel(self.bot, ctx.channel):
                await send_ephemeral_container(ctx, "This command can only be used in a support ticket channel.")
                return

            ticket_role = await get_ticket_role(self.bot, ctx.guild.id)
            if not ticket_role:
                await send_ephemeral_container(ctx, "Support system is not set up properly.")
                return

            ticket_creator_id = await get_ticket_creator(self.bot, ctx.channel.id)
            if not ticket_creator_id:
                await send_ephemeral_container(ctx, "Could not determine the ticket creator.")
                return

            from utils.tickets import get_ticket_creator_member

            ticket_creator = await get_ticket_creator_member(self.bot, ctx.guild, ctx.channel.id)

            if not ticket_creator:
                logger.warning(
                    f"Ticket creator {ticket_creator_id} could not be retrieved for channel {ctx.channel.id}"
                )

                class MockUser:
                    def __init__(self, user_id):
                        self.id = user_id
                        self.mention = f"<@{user_id}>"
                        self.display_name = "Unknown User"
                        self.name = "Unknown User"

                ticket_creator = MockUser(ticket_creator_id)

            invoker = ctx.author if isinstance(ctx, commands.Context) else ctx.user
            user_has_support = await user_has_support_role(self.bot, invoker)
            if not (invoker == ticket_creator or user_has_support):
                await send_ephemeral_container(ctx, "You do not have permission to close this ticket.")
                return

            from utils.tickets import get_ticket_info

            ticket_info = await get_ticket_info(self.bot, ctx.channel.id)

            if not ticket_info:
                await send_ephemeral_container(ctx, "Could not retrieve ticket information.")
                return

            ticket_data = {
                "creator_id": ticket_info["creator_id"],
                "ticket_number": ticket_info["ticket_number"],
                "category": ticket_info["category"],
                "subject": "N/A",
                "description": "N/A",
            }

            from views.ticket_views import close_ticket_channel

            class InteractionWrapper:
                def __init__(self, ctx):
                    self.channel = ctx.channel
                    self.guild = ctx.guild
                    self.user = ctx.author if isinstance(ctx, commands.Context) else ctx.user

            wrapper = InteractionWrapper(ctx)
            success, message = await close_ticket_channel(self.bot, wrapper, ticket_data)

            if not success:
                await send_ephemeral_container(ctx, f"Error closing ticket: {message}")

        except discord.Forbidden as e:
            logger.error(f"Forbidden error in close_ticket: {e}")
            await send_ephemeral_container(ctx, f"I don't have permission to delete the channel: {e}")
        except Exception as e:
            logger.error(f"Error in close_ticket: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @commands.hybrid_command(name="reopen", description="Reopen a closed ticket.")
    async def reopen_ticket(self, ctx: commands.Context):
        logger.info(f"Reopen ticket command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}")
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            if not await is_ticket_channel(self.bot, ctx.channel, include_closed=True):
                await send_ephemeral_container(ctx, "This command can only be used in a support ticket channel.")
                return

            invoker = ctx.author if isinstance(ctx, commands.Context) else ctx.user
            has_support_role = await user_has_support_role(self.bot, invoker)
            is_admin = invoker.guild_permissions.administrator

            if not (has_support_role or is_admin):
                await send_ephemeral_container(ctx, "Only staff can reopen tickets.")
                return

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT status, creator_id FROM ticket_instances WHERE channel_id = ?", (ctx.channel.id,)
                )
                result = await cur.fetchone()

                if not result:
                    await send_ephemeral_container(ctx, "Could not find ticket information.")
                    return

                status, creator_id = result

                if status == "open":
                    await send_ephemeral_container(ctx, "This ticket is already open.")
                    return

                await cur.execute(
                    """
                    UPDATE ticket_instances
                    SET status = 'open', closed_at = NULL
                    WHERE channel_id = ? AND status != 'open'
                    """,
                    (ctx.channel.id,),
                )
                if cur.rowcount == 0:
                    await send_ephemeral_container(ctx, "This ticket could not be reopened.")
                    return
                await self.bot.db.commit()

            if creator_id:
                creator = ctx.guild.get_member(creator_id)
                if creator:
                    try:
                        await ctx.channel.set_permissions(creator, send_messages=True, view_channel=True)
                        logger.info(f"Restored send_messages permission for user {creator_id} in ticket channel")
                    except Exception as perm_error:
                        logger.warning(f"Could not restore permissions for user: {perm_error}")

            reopen_layout = discord.ui.LayoutView()
            reopen_container = discord.ui.Container(accent_color=PURPLE_PRIMARY)
            reopen_container.add_item(discord.ui.TextDisplay("### Ticket Reopened"))
            reopen_container.add_item(discord.ui.Separator())
            reopen_container.add_item(discord.ui.TextDisplay(f"Reopened by {invoker.mention}"))
            reopen_layout.add_item(reopen_container)

            if isinstance(ctx, discord.Interaction):
                await ctx.followup.send(view=reopen_layout)
            else:
                await ctx.send(view=reopen_layout)

        except Exception as e:
            logger.error(f"Error in reopen_ticket: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @commands.hybrid_command(name="setlimit", description="Set the ticket limit per user.")
    @app_commands.describe(limit="Maximum number of tickets a user can have open (1-10)")
    @commands.has_permissions(administrator=True)
    async def set_limit(self, ctx: commands.Context, limit: int):
        logger.info(
            f"Set limit command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}: {limit}"
        )
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            if limit < 1 or limit > 10:
                await send_ephemeral_container(ctx, "Ticket limit must be between 1 and 10.")
                return

            async with self.bot.db.cursor() as cur:
                await cur.execute("UPDATE tickets SET ticket_limit = ? WHERE guild_id = ?", (limit, ctx.guild.id))

                if cur.rowcount == 0:
                    await send_ephemeral_container(ctx, "Please run `/setup` first to configure the ticket system.")
                    return

                await self.bot.db.commit()

            await send_container(
                ctx,
                "Ticket Limit Updated",
                f"New ticket limit: {limit} tickets per user\n\nUsers can now have up to {limit} open tickets at once.",
            )

        except Exception as e:
            logger.error(f"Error in set_limit: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @category.command(name="reset", description="Reset all ticket categories.")
    @commands.has_permissions(administrator=True)
    async def reset_categories(self, ctx: commands.Context):
        logger.info(
            f"Reset categories command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}"
        )
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            success, message = await reset_ticket_categories(self.bot, ctx.guild.id)

            title = "Categories Reset" if success else "Error"
            await send_container(ctx, title, message)

        except Exception as e:
            logger.error(f"Error in reset_categories: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @commands.hybrid_group(name="ticket", description="Manage tickets")
    async def ticket(self, ctx: commands.Context):
        """Manage tickets"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @ticket.command(name="transfer", description="Transfer ticket to another staff member.")
    @app_commands.describe(member="Staff member to transfer the ticket to")
    async def transfer_ticket(self, ctx: commands.Context, member: discord.Member):
        logger.info(
            f"Transfer ticket command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}"
        )
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            if not await is_ticket_channel(self.bot, ctx.channel):
                await send_ephemeral_container(ctx, "This command can only be used in a support ticket channel.")
                return

            invoker = ctx.author if isinstance(ctx, commands.Context) else ctx.user
            user_has_support = await user_has_support_role(self.bot, invoker)
            if not user_has_support:
                await send_ephemeral_container(ctx, "You do not have permission to transfer tickets.")
                return

            target_has_support = await user_has_support_role(self.bot, member)
            if not target_has_support:
                await send_ephemeral_container(ctx, f"{member.mention} does not have the support role.")
                return

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE ticket_instances
                    SET claimed_by = ?
                    WHERE channel_id = ? AND status = 'open'
                    """,
                    (member.id, ctx.channel.id),
                )
                if cur.rowcount == 0:
                    await send_ephemeral_container(ctx, "The ticket is no longer open.")
                    return
                await self.bot.db.commit()

            await send_channel_container(
                ctx.channel,
                "Ticket Transferred",
                f"Transferred by: {invoker.mention}\nTransferred to: {member.mention}\n\n{member.mention}, this ticket has been assigned to you for handling.",
            )

            await send_ephemeral_container(ctx, f"Ticket successfully transferred to {member.mention}.")

        except Exception as e:
            logger.error(f"Error in transfer_ticket: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @ticket.command(name="adduser", description="Add a user to the current ticket.")
    @app_commands.describe(user="User to add to the ticket")
    async def ticket_adduser(self, ctx: commands.Context, user: discord.Member):
        logger.info(f"Add user command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}")
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            if not await is_ticket_channel(self.bot, ctx.channel):
                await send_ephemeral_container(ctx, "This command can only be used in a support ticket channel.")
                return

            invoker = ctx.author if isinstance(ctx, commands.Context) else ctx.user
            user_has_support = await user_has_support_role(self.bot, invoker)

            if not user_has_support:
                await send_ephemeral_container(ctx, "Only support staff can add users to tickets.")
                return

            await ctx.channel.set_permissions(
                user, view_channel=True, send_messages=True, reason=f"User added to ticket by {invoker}"
            )

            await send_channel_container(
                ctx.channel,
                "User Added to Ticket",
                f"Added user: {user.mention}\nAdded by: {invoker.mention}\n\n{user.mention}, you have been added to this support ticket.",
            )

            await send_ephemeral_container(ctx, f"{user.mention} has been added to the ticket.")

        except Exception as e:
            logger.error(f"Error in add_user: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @ticket.command(name="removeuser", description="Remove a user from the current ticket.")
    @app_commands.describe(user="User to remove from the ticket")
    async def remove_user(self, ctx: commands.Context, user: discord.Member):
        logger.info(f"Remove user command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}")
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            if not await is_ticket_channel(self.bot, ctx.channel):
                await send_ephemeral_container(ctx, "This command can only be used in a support ticket channel.")
                return

            invoker = ctx.author if isinstance(ctx, commands.Context) else ctx.user
            user_has_support = await user_has_support_role(self.bot, invoker)

            if not user_has_support:
                await send_ephemeral_container(ctx, "Only support staff can remove users from tickets.")
                return

            await ctx.channel.set_permissions(user, overwrite=None, reason=f"User removed from ticket by {invoker}")

            await send_channel_container(
                ctx.channel, "User Removed from Ticket", f"Removed user: {user.mention}\\nRemoved by: {invoker.mention}"
            )

            await send_ephemeral_container(ctx, f"{user.mention} has been removed from the ticket.")

        except Exception as e:
            logger.error(f"Error in remove_user: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @commands.hybrid_command(name="variables", description="List all available variables for ticket messages.")
    async def list_variables(self, ctx: commands.Context):
        """Display all available variables that can be used in ticket messages"""
        logger.info(f"Variables command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}")
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            from utils.variables import format_variables_list

            variables_text = format_variables_list()

            title = "Ticket Variables"
            content = (
                "You can use these variables in ticket messages and they will be automatically replaced with actual values.\\n\\n"
                f"{variables_text}\\n\\n"
                "**Example Usage:**\\n"
                "```\\n"
                "Hello {username}! Your ticket #{ticketnumber} has been created.\\n"
                "Category: {category}\\n"
                "Server: {servername}\\n"
                "```"
            )

            await send_container(ctx, title, content, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in list_variables: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @ticket.command(name="info", description="Display detailed information about the current ticket.")
    async def ticket_info(self, ctx: commands.Context):
        logger.info(f"Ticket info command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}")

        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            if not await is_ticket_channel(self.bot, ctx.channel):
                await send_ephemeral_container(ctx, "This command can only be used in a support ticket channel.")
                return

            from utils.tickets import get_ticket_info

            ticket_info = await get_ticket_info(self.bot, ctx.channel.id)
            if not ticket_info:
                await send_ephemeral_container(ctx, "Could not retrieve ticket information.")
                return

            ticket_creator = ctx.guild.get_member(ticket_info["creator_id"])
            created_time = (
                discord.utils.parse_time(ticket_info["created_at"])
                if ticket_info["created_at"]
                else discord.utils.utcnow()
            )

            message_count = 0
            async for _ in ctx.channel.history(limit=None):
                message_count += 1

            accessible_users = []
            for member in ctx.guild.members:
                if ctx.channel.permissions_for(member).view_channel and not member.bot:
                    accessible_users.append(member)

            users_list = (
                ", ".join([user.mention for user in accessible_users[:5]])
                + (f" and {len(accessible_users) - 5} more..." if len(accessible_users) > 5 else "")
                if accessible_users
                else "No users with access"
            )

            content = (
                f"Channel: {ctx.channel.mention}\n"
                f"Ticket ID: `{ctx.channel.id}`\n\n"
                f"**Ticket Details**\n"
                f"Number: #{ticket_info['ticket_number']:04d}\n"
                f"Status: Open\n"
                f"Messages: {message_count}\n\n"
                f"**Creator Information**\n"
                f"User: {ticket_creator.mention if ticket_creator else 'Unknown'}\n"
                f"ID: `{ticket_info['creator_id']}`\n\n"
                f"**Timeline**\n"
                f"Created: {discord.utils.format_dt(created_time, 'R')}\n"
                f"Created Date: {created_time.strftime('%B %d, %Y')}\n\n"
                f"**Access List**\n"
                f"Users with access: {len(accessible_users)}\n"
                f"Users: {users_list}"
            )

            await send_container(ctx, "Ticket Information", content)

        except Exception as e:
            logger.error(f"Error in ticket_info: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @commands.hybrid_command(name="rename", description="Rename the current ticket channel.")
    @app_commands.describe(name="New name for the ticket channel")
    async def rename_ticket(self, ctx: commands.Context, *, name: str):
        logger.info(f"Rename ticket command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}")
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            if not await is_ticket_channel(self.bot, ctx.channel):
                await send_ephemeral_container(ctx, "This command can only be used in support ticket channels.")
                return

            invoker = ctx.author if isinstance(ctx, commands.Context) else ctx.user
            user_has_support = await user_has_support_role(self.bot, invoker)

            if not user_has_support:
                await send_ephemeral_container(
                    ctx,
                    "You don't have permission to rename this ticket. Only support staff members can rename tickets.",
                )
                return

            if len(name) > 100:
                await send_ephemeral_container(
                    ctx,
                    f"Channel name cannot exceed 100 characters. Your name: {len(name)} characters. Please shorten by {len(name) - 100} characters.",
                )
                return

            if len(name.strip()) == 0:
                await send_ephemeral_container(ctx, "Channel name cannot be empty. Please provide a valid name.")
                return

            def sanitize_channel_name(input_name: str) -> str:
                sanitized = input_name.lower().strip()

                sanitized = re.sub(r"\s+", "-", sanitized)

                sanitized = re.sub(r"[^a-z0-9\-_]", "", sanitized)

                sanitized = re.sub(r"-+", "-", sanitized)

                sanitized = sanitized.strip("-_")

                if not sanitized:
                    sanitized = "ticket-channel"

                if len(sanitized) > 100:
                    sanitized = sanitized[:100].rstrip("-_")

                return sanitized

            sanitized_name = sanitize_channel_name(name)

            if not sanitized_name:
                await send_ephemeral_container(
                    ctx,
                    "The name contains only invalid characters. Valid characters are letters (a-z), numbers (0-9), dashes (-), and underscores (_). Example: `billing-issue` or `technical_support`",
                )
                return

            old_name = ctx.channel.name
            await ctx.channel.edit(name=sanitized_name)

            await send_container(ctx, "Ticket Renamed", f"Previous name: {old_name}\nNew name: {sanitized_name}")

        except discord.Forbidden:
            await send_ephemeral_container(ctx, "I don't have permission to rename this channel.")
        except Exception as e:
            logger.error(f"Error in rename_ticket: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @commands.hybrid_command(name="claim", description="Claim the current ticket.")
    async def claim_ticket(self, ctx: commands.Context):
        logger.info(f"Claim ticket command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}")
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            if not await is_ticket_channel(self.bot, ctx.channel):
                await send_ephemeral_container(ctx, "This command can only be used in support ticket channels.")
                return

            invoker = ctx.author if isinstance(ctx, commands.Context) else ctx.user

            has_support_role = await user_has_support_role(self.bot, invoker)
            is_admin = invoker.guild_permissions.administrator

            if not (has_support_role or is_admin):
                await send_ephemeral_container(
                    ctx, "Only support staff can claim tickets. Contact an administrator to get the support role."
                )
                return

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT claimed_by, ticket_number, creator_id, category FROM ticket_instances WHERE channel_id = ?",
                    (ctx.channel.id,),
                )
                ticket_result = await cur.fetchone()

                if not ticket_result:
                    await send_ephemeral_container(
                        ctx, "Could not find ticket information. This ticket may not be properly registered."
                    )
                    return

                current_claimer_id, ticket_number, creator_id, category = ticket_result

                if current_claimer_id:
                    if current_claimer_id == invoker.id:
                        await send_ephemeral_container(
                            ctx, "You have already claimed this ticket. Continue providing support."
                        )
                        return

                    claimer = ctx.guild.get_member(current_claimer_id)
                    await send_ephemeral_container(
                        ctx,
                        f"This ticket is already being handled by {claimer.display_name if claimer else 'another agent'}. Use `/ticket transfer @new_agent` if needed.",
                    )
                    return

                await cur.execute(
                    "UPDATE ticket_instances SET claimed_by = ? WHERE channel_id = ? AND claimed_by IS NULL",
                    (invoker.id, ctx.channel.id),
                )

                if cur.rowcount == 0:
                    await send_ephemeral_container(
                        ctx,
                        "Another agent claimed this ticket at the same time. Please refresh and try again if needed.",
                    )
                    return

                await self.bot.db.commit()

            ticket_creator = ctx.guild.get_member(creator_id)

            if ticket_creator:
                creator_mention = ticket_creator.mention
            elif creator_id:
                creator_mention = f"<@{creator_id}>"
            else:
                creator_mention = "**Ticket Creator**"

            claim_message = f"{creator_mention} your ticket has been claimed by {invoker.mention}"

            await ctx.channel.send(claim_message)

            await send_ephemeral_container(ctx, f"You have successfully claimed ticket #{ticket_number:04d}!")

        except Exception as e:
            logger.error(f"Error in claim_ticket: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @commands.hybrid_group(name="blacklist", description="Manage ticket blacklist")
    @commands.has_permissions(administrator=True)
    async def blacklist(self, ctx: commands.Context):
        """Manage ticket blacklist"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @blacklist.command(name="add", description="Blacklist a user from creating tickets.")
    @app_commands.describe(user="User to blacklist from creating tickets")
    @commands.has_permissions(administrator=True)
    async def blacklist_user(self, ctx: commands.Context, user: discord.Member):
        logger.info(
            f"Blacklist user command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}: {user}"
        )
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?", (ctx.guild.id, user.id)
                )
                if await cur.fetchone():
                    await send_ephemeral_container(
                        ctx, f"{user.mention} is already blacklisted. This user cannot create tickets."
                    )
                    return

                await cur.execute(
                    "INSERT INTO ticket_blacklist (guild_id, user_id, blacklisted_by, blacklisted_at) VALUES (?, ?, ?, ?)",
                    (
                        ctx.guild.id,
                        user.id,
                        ctx.author.id if isinstance(ctx, commands.Context) else ctx.user.id,
                        discord.utils.utcnow().isoformat(),
                    ),
                )
                await self.bot.db.commit()

            await send_container(
                ctx,
                "User Blacklisted",
                f"{user.mention} has been blacklisted from creating tickets.\n\nUser: {user.display_name}\nEffect: User cannot create new tickets. Existing tickets remain unaffected.",
            )

        except Exception as e:
            logger.error(f"Error in blacklist_user: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @blacklist.command(name="remove", description="Remove a user from the ticket blacklist.")
    @app_commands.describe(user="User to remove from blacklist")
    @commands.has_permissions(administrator=True)
    async def blacklist_remove_user(self, ctx: commands.Context, user: discord.Member):
        logger.info(
            f"Blacklist remove user command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}: {user}"
        )
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT blacklisted_by, blacklisted_at FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?",
                    (ctx.guild.id, user.id),
                )
                result = await cur.fetchone()

                if not result:
                    await send_ephemeral_container(
                        ctx, f"{user.mention} is not currently blacklisted. They can already create tickets normally."
                    )
                    return

                await cur.execute(
                    "DELETE FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?", (ctx.guild.id, user.id)
                )
                await self.bot.db.commit()

            await send_container(
                ctx,
                "User Removed from Blacklist",
                f"{user.mention} has been removed from the ticket blacklist.\n\nUser: {user.display_name}\nEffect: User can now create tickets normally.",
            )

        except Exception as e:
            logger.error(f"Error in blacklist_remove_user: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @blacklist.command(name="list", description="View all blacklisted users in this server.")
    @commands.has_permissions(administrator=True)
    async def blacklist_list(self, ctx: commands.Context):
        logger.info(
            f"Blacklist list command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}"
        )
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT user_id, blacklisted_by, blacklisted_at FROM ticket_blacklist WHERE guild_id = ? ORDER BY blacklisted_at DESC",
                    (ctx.guild.id,),
                )
                blacklisted = await cur.fetchall()

            if not blacklisted:
                await send_ephemeral_container(
                    ctx, "No users are currently blacklisted. All members can create tickets normally."
                )
                return

            blacklist_text = ""
            for user_id, blacklisted_by, blacklisted_at in blacklisted[:10]:
                user = ctx.guild.get_member(user_id)
                blacklister = ctx.guild.get_member(blacklisted_by)
                user_display = user.mention if user else f"<@{user_id}>"
                blacklister_display = blacklister.display_name if blacklister else "Unknown"
                try:
                    blacklist_date = discord.utils.parse_time(blacklisted_at)
                    date_display = discord.utils.format_dt(blacklist_date, "R")
                except (TypeError, ValueError):
                    date_display = "Unknown date"
                blacklist_text += f"- {user_display} - by {blacklister_display} {date_display}\n"

            content = f"{len(blacklisted)} user(s) blacklisted\n\n{blacklist_text}" + (
                f"\n*Showing first 10 of {len(blacklisted)}*" if len(blacklisted) > 10 else ""
            )
            await send_container(ctx, "Blacklisted Users", content)

        except Exception as e:
            logger.error(f"Error in blacklist_list: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @commands.hybrid_command(name="faq", description="Display frequently asked questions.")
    async def faq(self, ctx: commands.Context):
        logger.info(f"FAQ command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}")
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            user_id = ctx.author.id if isinstance(ctx, commands.Context) else ctx.user.id
            view = FAQLayout(self.bot, user_id)

            if isinstance(ctx, discord.Interaction):
                await ctx.followup.send(view=view, ephemeral=True)
            else:
                await ctx.send(view=view)

        except Exception as e:
            logger.error(f"Error in faq: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @commands.hybrid_command(name="maintenance", description="Toggle maintenance mode to disable ticket creation.")
    @commands.has_permissions(administrator=True)
    async def maintenance_mode(self, ctx: commands.Context):
        logger.info(
            f"Maintenance mode command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}"
        )
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT maintenance_mode FROM tickets WHERE guild_id = ?", (ctx.guild.id,))
                result = await cur.fetchone()

                if not result:
                    await send_ephemeral_container(ctx, "Support system is not set up. Use `/setup` first.")
                    return

                current_mode = bool(result[0]) if result[0] is not None else False
                new_mode = not current_mode

                await cur.execute(
                    "UPDATE tickets SET maintenance_mode = ? WHERE guild_id = ?", (new_mode, ctx.guild.id)
                )
                await self.bot.db.commit()

            status = "ENABLED" if new_mode else "DISABLED"
            effect = (
                "Ticket creation is now disabled. Users cannot create new tickets."
                if new_mode
                else "Ticket creation is now enabled. Users can create tickets normally."
            )
            await send_container(
                ctx, f"Maintenance Mode {status}", f"Maintenance mode has been {status.lower()}.\n\n{effect}"
            )

        except Exception as e:
            logger.error(f"Error in maintenance_mode: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @commands.hybrid_command(name="announce", description="Send an announcement to all open tickets.")
    @app_commands.describe(message="The announcement message to send to all open tickets")
    @commands.has_permissions(administrator=True)
    async def announce(self, ctx: commands.Context, *, message: str):
        logger.info(f"Announce command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}")
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            if len(message) > 2000:
                await send_ephemeral_container(ctx, "Announcement message cannot exceed 2000 characters.")
                return

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT channel_id, ticket_number, creator_id FROM ticket_instances WHERE guild_id = ? AND status = 'open'",
                    (ctx.guild.id,),
                )
                tickets = await cur.fetchall()

            if not tickets:
                await send_ephemeral_container(ctx, "No open tickets found to send announcements to.")
                return

            success_count = 0
            failed_count = 0

            announcement_content = f"Official announcement from {ctx.guild.name} support team:\n\n{message}"

            for channel_id, ticket_number, creator_id in tickets:
                try:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        await send_channel_container(channel, "System Announcement", announcement_content)
                        success_count += 1
                except Exception as e:
                    logger.warning(f"Failed to send announcement to ticket #{ticket_number:04d}: {e}")
                    failed_count += 1

            await send_container(
                ctx,
                "Announcement Sent",
                f"Announcement delivered to open tickets.\n\nSuccessfully sent: {success_count} tickets\nFailed to send: {failed_count} tickets\nTotal tickets: {len(tickets)}",
            )

        except Exception as e:
            logger.error(f"Error in announce: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @commands.hybrid_group(name="supportrole", description="Manage support roles")
    @commands.has_permissions(administrator=True)
    async def supportrole(self, ctx: commands.Context):
        """Manage support roles"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @supportrole.command(name="add", description="Add an additional support role.")
    @app_commands.describe(role="Role to add as additional support staff")
    @commands.has_permissions(administrator=True)
    async def support_role_add(self, ctx: commands.Context, role: discord.Role):
        logger.info(
            f"Support role add command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}"
        )
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT role_id FROM tickets WHERE guild_id = ?", (ctx.guild.id,))
                primary_role = await cur.fetchone()

                if primary_role and primary_role[0] == role.id:
                    await send_ephemeral_container(
                        ctx,
                        f"{role.mention} is already the primary support role. Use this command to add additional support roles only.",
                    )
                    return

                await cur.execute(
                    "SELECT 1 FROM additional_support_roles WHERE guild_id = ? AND role_id = ?", (ctx.guild.id, role.id)
                )
                if await cur.fetchone():
                    await send_ephemeral_container(
                        ctx,
                        f"{role.mention} is already an additional support role. This role can already manage tickets.",
                    )
                    return

            from utils.database import add_support_role

            success, message = await add_support_role(self.bot, ctx.guild.id, role.id)

            title = "Support Role Added" if success else "Error"
            content = (
                f"{role.mention} has been added as an additional support role. Members with this role can now manage tickets."
                if success
                else message
            )
            await send_container(ctx, title, content)

        except Exception as e:
            logger.error(f"Error in support_role_add: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @supportrole.command(name="remove", description="Remove an additional support role.")
    @app_commands.describe(role="Role to remove from additional support staff")
    @commands.has_permissions(administrator=True)
    async def support_role_remove(self, ctx: commands.Context, role: discord.Role):
        logger.info(
            f"Support role remove command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}"
        )
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT role_id FROM tickets WHERE guild_id = ?", (ctx.guild.id,))
                primary_role = await cur.fetchone()

                if primary_role and primary_role[0] == role.id:
                    await send_ephemeral_container(
                        ctx,
                        f"{role.mention} is the primary support role and cannot be removed. Use `/setup` to change the primary support role.",
                    )
                    return

            from utils.database import remove_support_role

            success, message = await remove_support_role(self.bot, ctx.guild.id, role.id)

            title = "Support Role Removed" if success else "Error"
            content = (
                f"{role.mention} has been removed from additional support roles. Members with this role can no longer manage tickets."
                if success
                else message
            )
            await send_container(ctx, title, content)

        except Exception as e:
            logger.error(f"Error in support_role_remove: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @supportrole.command(name="list", description="List all support roles.")
    @commands.has_permissions(administrator=True)
    async def support_role_list(self, ctx: commands.Context):
        logger.info(
            f"Support role list command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}"
        )
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT role_id FROM tickets WHERE guild_id = ?", (ctx.guild.id,))
                primary_role_result = await cur.fetchone()

            from utils.database import get_additional_support_roles

            additional_roles = await get_additional_support_roles(self.bot, ctx.guild.id)

            primary_role_text = "None configured"
            if primary_role_result and primary_role_result[0]:
                primary_role = ctx.guild.get_role(primary_role_result[0])
                primary_role_text = (
                    primary_role.mention if primary_role else f"<@&{primary_role_result[0]}> (Role deleted)"
                )

            additional_role_text = "None configured"
            if additional_roles:
                additional_role_list = [f"- {role.mention}" for role in additional_roles]
                additional_role_text = "\n".join(additional_role_list) if additional_role_list else "None configured"

            total_roles = 1 if primary_role_result and primary_role_result[0] else 0
            total_roles += len(additional_roles)

            content = (
                f"All roles that can manage tickets in this server:\n\n"
                f"**Primary Support Role**\n{primary_role_text}\n\n"
                f"**Additional Support Roles**\n{additional_role_text}\n\n"
                f"**Summary**\nTotal Support Roles: {total_roles}\nPrimary Role: {'Configured' if primary_role_result and primary_role_result[0] else 'Not configured'}\nAdditional Roles: {len(additional_roles)}"
            )

            await send_container(ctx, "Support Roles", content)

        except Exception as e:
            logger.error(f"Error in support_role_list: {e}")
            await send_ephemeral_container(ctx, f"An error occurred: {e}")

    @tasks.loop(seconds=30)
    async def reminder_dispatcher(self):
        """Deliver persisted reminders, including those that survive a restart."""
        now = discord.utils.utcnow().isoformat()
        async with self.bot.db.cursor() as cur:
            await cur.execute(
                """
                SELECT id, channel_id, creator_id, message
                FROM ticket_reminders
                WHERE delivered_at IS NULL AND attempts < 5 AND due_at <= ?
                ORDER BY due_at
                LIMIT 25
                """,
                (now,),
            )
            reminders = await cur.fetchall()

        for reminder_id, channel_id, creator_id, message in reminders:
            channel = self.bot.get_channel(channel_id)
            try:
                if channel is None:
                    raise RuntimeError("Reminder channel is unavailable")

                from utils.helpers import create_container_view

                view = create_container_view(
                    "Scheduled Reminder",
                    f"This is your scheduled follow-up reminder.\n\n{message}",
                )
                await channel.send(
                    content=f"<@{creator_id}>",
                    view=view,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False,
                        roles=False,
                        users=[discord.Object(id=creator_id)],
                        replied_user=False,
                    ),
                )
                async with self.bot.db.cursor() as cur:
                    await cur.execute(
                        """
                        UPDATE ticket_reminders
                        SET delivered_at = ?, attempts = attempts + 1, last_error = NULL
                        WHERE id = ? AND delivered_at IS NULL
                        """,
                        (discord.utils.utcnow().isoformat(), reminder_id),
                    )
                    await self.bot.db.commit()
            except Exception as exc:
                logger.warning("Reminder %s delivery failed: %s", reminder_id, exc)
                async with self.bot.db.cursor() as cur:
                    await cur.execute(
                        """
                        UPDATE ticket_reminders
                        SET attempts = attempts + 1, last_error = ?
                        WHERE id = ? AND delivered_at IS NULL
                        """,
                        (str(exc)[:500], reminder_id),
                    )
                    await self.bot.db.commit()

    @reminder_dispatcher.before_loop
    async def before_reminder_dispatcher(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="remind", description="Set an advanced reminder for ticket follow-up.")
    @app_commands.describe(time="Time format: 5m, 1h, 2d (minutes, hours, days)", message="Reminder message (optional)")
    async def remind(self, ctx: commands.Context, time: str, *, message: Optional[str] = None):
        logger.info(f"Remind command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}")
        try:
            if isinstance(ctx, discord.Interaction):
                await ctx.response.defer(ephemeral=True)

            invoker = ctx.author
            user_has_support = await user_has_support_role(self.bot, invoker)
            in_ticket_channel = await is_ticket_channel(self.bot, ctx.channel)

            if not (user_has_support or in_ticket_channel):
                error_message = "<:icons_Wrong:1382701332955402341> | You can only set reminders in ticket channels or if you have the support role."
                if isinstance(ctx, discord.Interaction):
                    await ctx.followup.send(error_message, ephemeral=True)
                else:
                    await ctx.send(error_message, ephemeral=True)
                return

            import re

            time_pattern = r"^(\d+)([mhd])$"
            match = re.match(time_pattern, time.lower())

            if not match:
                error_message = (
                    "<:icons_Wrong:1382701332955402341> | Invalid time format. Use: 5m (minutes), 1h (hours), 2d (days)"
                )
                if isinstance(ctx, discord.Interaction):
                    await ctx.followup.send(error_message, ephemeral=True)
                else:
                    await ctx.send(error_message, ephemeral=True)
                return

            amount = int(match.group(1))
            unit = match.group(2)

            multipliers = {"m": 60, "h": 3600, "d": 86400}
            delay_seconds = amount * multipliers[unit]

            if delay_seconds < 60:  # Minimum 1 minute
                error_message = "<:icons_Wrong:1382701332955402341> | Reminder time must be at least 1 minute."
                if isinstance(ctx, discord.Interaction):
                    await ctx.followup.send(error_message, ephemeral=True)
                else:
                    await ctx.send(error_message, ephemeral=True)
                return

            if delay_seconds > 604800:  # Maximum 7 days
                error_message = "<:icons_Wrong:1382701332955402341> | Reminder time cannot exceed 7 days."
                if isinstance(ctx, discord.Interaction):
                    await ctx.followup.send(error_message, ephemeral=True)
                else:
                    await ctx.send(error_message, ephemeral=True)
                return

            if not message:
                message = "<:icons_clock:1382701751206936697> **Reminder:** This is your scheduled follow-up reminder."
            if len(message) > 1000:
                await send_ephemeral_container(ctx, "Reminder messages cannot exceed 1,000 characters.")
                return

            current_time = utc_to_gmt(discord.utils.utcnow())
            remind_time = current_time + timedelta(seconds=delay_seconds)

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO ticket_reminders
                    (guild_id, channel_id, creator_id, message, due_at, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ctx.guild.id,
                        ctx.channel.id,
                        invoker.id,
                        message,
                        remind_time.isoformat(),
                        invoker.id,
                        current_time.isoformat(),
                    ),
                )
                await self.bot.db.commit()

            time_units = {"m": "minutes", "h": "hours", "d": "days"}
            content = (
                f"Your reminder has been scheduled successfully!\n\n"
                f"Trigger Time: {discord.utils.format_dt(remind_time, 'F')}\n"
                f"In: {amount} {time_units[unit]}\n"
                f"Channel: {ctx.channel.mention}\n\n"
                f"Message: *{message}*"
            )
            await send_container(ctx, "Reminder Set", content)

        except Exception as e:
            logger.error(f"Error in remind: {e}")
            error_message = f"<:icons_Wrong:1382701332955402341> | An error occurred: {str(e)}"

            try:
                if isinstance(ctx, discord.Interaction):
                    await send_ephemeral_container(ctx, error_message)
                else:
                    await ctx.send(error_message)
            except Exception as send_error:
                logger.error(f"Failed to send error message: {send_error}")


class FAQLayout(ui.LayoutView):
    def __init__(self, bot, user_id):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id

        self.container = ui.Container(accent_color=PURPLE_PRIMARY)
        self.setup_main_content()
        self.add_item(self.container)

    def setup_main_content(self):
        self.container.clear_items()

        self.container.add_item(ui.TextDisplay("### Frequently Asked Questions"))
        self.container.add_item(ui.Separator())

        welcome_section = ui.Section(accessory=ui.Thumbnail(media=self.bot.user.display_avatar.url))
        welcome_section.add_item(
            ui.TextDisplay("Welcome to our FAQ section! Select a category below to find answers to common questions.")
        )
        self.container.add_item(welcome_section)

        self.container.add_item(ui.Separator())

        self.category_select = ui.ActionRow(
            ui.Select(
                placeholder="Select a FAQ category...",
                options=[
                    discord.SelectOption(label="Home", value="home", description="Return to main FAQ page"),
                    discord.SelectOption(
                        label="Getting Started",
                        value="getting_started",
                        description="Creating tickets, basics & first steps",
                    ),
                    discord.SelectOption(
                        label="Response Times", value="response_times", description="Response times & what to expect"
                    ),
                    discord.SelectOption(
                        label="Ticket Management",
                        value="ticket_management",
                        description="Managing tickets, closing & user access",
                    ),
                    discord.SelectOption(
                        label="Features & Settings",
                        value="features_settings",
                        description="Transcripts, limits & features",
                    ),
                    discord.SelectOption(
                        label="Troubleshooting", value="troubleshooting", description="Common issues, solutions & fixes"
                    ),
                ],
            )
        )
        self.category_select.children[0].callback = self.category_callback
        self.container.add_item(self.category_select)

    async def category_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await send_ephemeral_container(interaction, "You cannot interact with this menu.")
            return

        choice = interaction.data.get("values", ["home"])[0] if interaction.data else "home"

        if choice == "home":
            self.setup_main_content()
            await interaction.response.edit_message(view=self)
        elif choice == "getting_started":
            await self.show_getting_started(interaction)
        elif choice == "response_times":
            await self.show_response_times(interaction)
        elif choice == "ticket_management":
            await self.show_ticket_management(interaction)
        elif choice == "features_settings":
            await self.show_features_settings(interaction)
        elif choice == "troubleshooting":
            await self.show_troubleshooting(interaction)

    async def show_getting_started(self, interaction: discord.Interaction):
        try:
            self.container.clear_items()

            self.container.add_item(ui.TextDisplay("### Getting Started with Support Tickets"))
            self.container.add_item(ui.Separator())

            content = (
                "**How do I create a ticket?**\n"
                "Look for the support panel in your server, click the dropdown or button for your issue type, "
                "fill out the ticket creation form, then submit. A private channel will be created where you can "
                "start chatting with our support team.\n\n"
                "**What happens after I create a ticket?**\n"
                "A private channel is created just for you where only you and support staff can see it. "
                "You'll receive a confirmation message and our support team gets notified immediately.\n\n"
                "**Pro Tips for New Users**\n"
                "Be clear and detailed in your ticket description. Choose the right category for faster help. "
                "Stay in your ticket channel for updates!"
            )
            self.container.add_item(ui.TextDisplay(content))

            self.container.add_item(ui.Separator())
            self.container.add_item(self.category_select)

            await interaction.response.edit_message(view=self)
        except Exception as e:
            logger.error(f"Error in show_getting_started: {e}")

    async def show_response_times(self, interaction: discord.Interaction):
        try:
            self.container.clear_items()

            self.container.add_item(ui.TextDisplay("### Response Times & Expectations"))
            self.container.add_item(ui.Separator())

            content = (
                "**What to Expect**\n"
                "After creating a ticket, our support team is notified immediately. "
                "A team member will claim your ticket and assist you as soon as possible.\n\n"
                "**General Response Times**\n"
                "Most tickets receive a response within a few hours. "
                "Response times may vary based on ticket volume and staff availability. "
                "You'll be notified when your ticket is claimed.\n\n"
                "**Tips for Faster Help**\n"
                "Choose the correct category for your issue. "
                "Provide clear details in your description. "
                "Include any relevant screenshots or files. "
                "Stay in your ticket channel for updates."
            )
            self.container.add_item(ui.TextDisplay(content))

            self.container.add_item(ui.Separator())
            self.container.add_item(self.category_select)

            await interaction.response.edit_message(view=self)
        except Exception as e:
            logger.error(f"Error in show_response_times: {e}")

    async def show_ticket_management(self, interaction: discord.Interaction):
        try:
            self.container.clear_items()

            self.container.add_item(ui.TextDisplay("### Ticket Management Guide"))
            self.container.add_item(ui.Separator())

            content = (
                "**Managing Your Tickets**\n"
                "`/ticket info` View ticket information\n"
                "`/ticket adduser @username` Add users to your ticket\n"
                "`/rename New Name` Rename your ticket\n"
                "`/close` Close your ticket\n"
                "`/ticket transfer @user` Transfer ownership\n\n"
                "**Ticket Limits & Rules**\n"
                "Each user can have multiple open tickets based on server limits. "
                "Tickets remain open until a user or staff member closes them, and rate limiting prevents spam. "
                "Transcripts are generated when staff delete a closed ticket.\n\n"
                "**Best Practices**\n"
                "Keep conversations in your ticket channel and provide screenshots or files when helpful. "
                "Update your ticket if the situation changes and close tickets when the issue is resolved."
            )
            self.container.add_item(ui.TextDisplay(content))

            self.container.add_item(ui.Separator())
            self.container.add_item(self.category_select)

            await interaction.response.edit_message(view=self)
        except Exception as e:
            logger.error(f"Error in show_ticket_management: {e}")

    async def show_features_settings(self, interaction: discord.Interaction):
        try:
            self.container.clear_items()

            self.container.add_item(ui.TextDisplay("### Features & Settings"))
            self.container.add_item(ui.Separator())

            content = (
                "**Transcript System**\n"
                "Full conversation logs are generated and sent to the creator when staff delete a closed ticket. "
                "Includes all messages, files, and embeds. Perfect for record keeping and formatted for easy reading.\n\n"
                "**Advanced Features**\n"
                "`Categories` Organized support types\n"
                "`Staff Assignment` Claim and transfer tickets\n"
                "`User Management` Add/remove ticket participants\n"
                "`Persistent Reminders` Restart-safe follow-up scheduling\n"
                "`Custom Branding` Server-specific appearance"
            )
            self.container.add_item(ui.TextDisplay(content))

            self.container.add_item(ui.Separator())
            self.container.add_item(self.category_select)

            await interaction.response.edit_message(view=self)
        except Exception as e:
            logger.error(f"Error in show_features_settings: {e}")

    async def show_troubleshooting(self, interaction: discord.Interaction):
        try:
            self.container.clear_items()

            self.container.add_item(ui.TextDisplay("### Troubleshooting Common Issues"))
            self.container.add_item(ui.Separator())

            content = (
                "**Common Problems & Solutions**\n"
                "`Can't create ticket` Check if you've reached the limit\n"
                "`No response` Staff will respond as soon as possible\n"
                "`Can't see transcript` Check your DM privacy settings\n"
                "`Permission errors` Contact server administrators\n"
                "`Bot not responding` Try again in a few moments\n\n"
                "**Quick Fixes**\n"
                "`Refresh Discord` Close and reopen the app\n"
                "`Clear Cache` Clear Discord's cache and restart\n"
                "`Check Permissions` Verify bot has required permissions\n"
                "`Wait and Retry` Some issues resolve automatically\n"
                "`Update Discord` Use the latest version\n\n"
                "**Still need help?**\n"
                "Can't find what you're looking for? Create a ticket for personalized assistance. "
                "Our expert support team is here to help and we're committed to resolving your issues."
            )
            self.container.add_item(ui.TextDisplay(content))

            self.container.add_item(ui.Separator())
            self.container.add_item(self.category_select)

            await interaction.response.edit_message(view=self)
        except Exception as e:
            logger.error(f"Error in show_troubleshooting: {e}")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_timeout(self):
        pass


async def setup(bot):
    await bot.add_cog(SupportSystem(bot))
