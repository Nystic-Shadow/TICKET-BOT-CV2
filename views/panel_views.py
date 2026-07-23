import discord
from discord import ui
import logging
from utils.helpers import check_rate_limit, send_ephemeral_container
from utils.database import get_user_open_tickets
from views.modals import TicketModal
from utils.config import config
from utils.application_emojis import resolve_component_emoji, resolve_emojis
from utils.theme import PURPLE_PRIMARY

logger = logging.getLogger("discord")

DEFAULT_TICKET_EMOJI = "<:Ticket_icons:1382703084815257610>"


class PersistentTicketSelect(discord.ui.Select):
    """Persistent select menu that works after bot restarts"""

    def __init__(self):
        super().__init__(
            placeholder="Select a category",
            options=[discord.SelectOption(label="Loading...", value="loading")],
            custom_id="ticket_category_select",
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        try:
            bot = interaction.client
            category = self.values[0]

            if category == "loading":
                await send_ephemeral_container(interaction, "Please wait for the panel to load.")
                return

            logger.info(f"Persistent select callback triggered by {interaction.user.id} for category {category}")

            async with bot.db.cursor() as cur:
                await cur.execute("SELECT maintenance_mode FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()
                maintenance_mode = bool(result[0]) if result and result[0] is not None else False

            if maintenance_mode:
                await send_ephemeral_container(
                    interaction,
                    "<:icons_wrench:1382702984940617738> The ticket system is currently under maintenance. Please try again later.",
                )
                return

            if await check_rate_limit(bot, interaction.guild.id, interaction.user.id):
                await send_ephemeral_container(
                    interaction,
                    "<:icons_Wrong:1382701332955402341> You're creating tickets too quickly. Please wait 60 seconds before creating another ticket.",
                )
                return

            async with bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?",
                    (interaction.guild.id, interaction.user.id),
                )
                if await cur.fetchone():
                    await send_ephemeral_container(
                        interaction,
                        "<:icons_Wrong:1382701332955402341> You are blacklisted from creating tickets in this server.",
                    )
                    return

            open_tickets = await get_user_open_tickets(bot, interaction.guild.id, interaction.user.id)

            async with bot.db.cursor() as cur:
                await cur.execute("SELECT ticket_limit FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()
                ticket_limit = result[0] if result else 3

            if open_tickets >= ticket_limit:
                await send_ephemeral_container(
                    interaction,
                    f"<:Ticket_icons:1382703084815257610> You already have {open_tickets} open tickets. Please close some before creating new ones.",
                )
                return

            confirmation_view = TicketConfirmationView(bot, category, interaction.guild.id, interaction.user.id)
            await interaction.response.send_message(view=confirmation_view, ephemeral=True)
            logger.info(f"Confirmation view sent for category {category}")

        except Exception as e:
            import traceback

            logger.error(f"Error in persistent select callback: {traceback.format_exc()}")
            try:
                await send_ephemeral_container(interaction, f"<:icons_Wrong:1382701332955402341> Error: {str(e)[:150]}")
            except Exception:
                pass


class PersistentTicketPanelView(discord.ui.View):
    """Persistent view for ticket panels - register once on startup"""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(PersistentTicketSelect())


class PersistentTicketButton(discord.ui.Button):
    """Persistent button that works after bot restarts"""

    def __init__(self, category: str, emoji: str = None, row: int = 0):
        display_emoji = resolve_component_emoji(DEFAULT_TICKET_EMOJI)
        if emoji and emoji.strip():
            if emoji.startswith(("<:", "<a:")) and emoji.endswith(">") and ":" in emoji:
                display_emoji = resolve_component_emoji(emoji)
            elif len(emoji) <= 4 and not emoji.startswith("<"):
                display_emoji = resolve_component_emoji(emoji)

        super().__init__(
            label=category,
            style=discord.ButtonStyle.primary,
            emoji=display_emoji,
            custom_id=f"ticket_button_{category}",
            row=row // 5,
        )
        self.category = category

    async def callback(self, interaction: discord.Interaction):
        try:
            bot = interaction.client
            logger.info(f"Persistent button callback triggered by {interaction.user.id} for category {self.category}")

            async with bot.db.cursor() as cur:
                await cur.execute("SELECT maintenance_mode FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()
                maintenance_mode = bool(result[0]) if result and result[0] is not None else False

            if maintenance_mode:
                await send_ephemeral_container(
                    interaction,
                    "<:icons_wrench:1382702984940617738> The ticket system is currently under maintenance. Please try again later.",
                )
                return

            if await check_rate_limit(bot, interaction.guild.id, interaction.user.id):
                await send_ephemeral_container(
                    interaction,
                    "<:icons_Wrong:1382701332955402341> You're creating tickets too quickly. Please wait 60 seconds before creating another ticket.",
                )
                return

            async with bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?",
                    (interaction.guild.id, interaction.user.id),
                )
                if await cur.fetchone():
                    await send_ephemeral_container(
                        interaction,
                        "<:icons_Wrong:1382701332955402341> You are blacklisted from creating tickets in this server.",
                    )
                    return

            open_tickets = await get_user_open_tickets(bot, interaction.guild.id, interaction.user.id)

            async with bot.db.cursor() as cur:
                await cur.execute("SELECT ticket_limit FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()
                ticket_limit = result[0] if result else 3

            if open_tickets >= ticket_limit:
                await send_ephemeral_container(
                    interaction,
                    f"<:Ticket_icons:1382703084815257610> You already have {open_tickets} open tickets. Please close some before creating new ones.",
                )
                return

            confirmation_view = TicketConfirmationView(bot, self.category, interaction.guild.id, interaction.user.id)
            await interaction.response.send_message(view=confirmation_view, ephemeral=True)
            logger.info(f"Confirmation view sent for category {self.category}")

        except Exception as e:
            import traceback

            logger.error(f"Error in persistent button callback: {traceback.format_exc()}")
            try:
                await send_ephemeral_container(interaction, f"<:icons_Wrong:1382701332955402341> Error: {str(e)[:150]}")
            except Exception:
                pass


class TicketConfirmationView(ui.LayoutView):
    def __init__(self, bot, category, guild_id, user_id):
        super().__init__(timeout=180)
        self.bot = bot
        self.category = category
        self.guild_id = guild_id
        self.user_id = user_id
        self.confirmed = False
        self.setup_content()

    def setup_content(self):
        self.container = ui.Container(accent_colour=PURPLE_PRIMARY)
        self.add_item(self.container)

        self.container.add_item(ui.TextDisplay("### Support Rules"))
        self.container.add_item(ui.Separator())

        self.container.add_item(ui.TextDisplay(resolve_emojis(config.TICKET_RULES_TEXT)))
        self.container.add_item(ui.Separator())

        button_row = ui.ActionRow(
            ui.Button(label="Open Ticket", style=discord.ButtonStyle.primary, custom_id="confirm_open_ticket"),
            ui.Button(label="Cancel", style=discord.ButtonStyle.danger, custom_id="cancel_ticket"),
        )

        button_row.children[0].callback = self.confirm_callback
        button_row.children[1].callback = self.cancel_callback

        self.container.add_item(button_row)

    async def confirm_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await send_ephemeral_container(interaction, "You cannot interact with this menu.")
            return

        self.confirmed = True
        modal = TicketModal(self.bot, self.category, self.guild_id)
        await interaction.response.send_modal(modal)
        self.stop()

    async def cancel_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await send_ephemeral_container(interaction, "You cannot interact with this menu.")
            return

        await interaction.response.edit_message(view=None, content="Ticket creation cancelled.")
        self.stop()


class TicketPanelView(discord.ui.View):
    def __init__(self, bot, categories, guild_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.categories = categories
        self.guild_id = guild_id

        if len(categories) <= 25:
            self.add_item(TicketCategorySelect(bot, categories, guild_id))


class TicketCategorySelect(discord.ui.Select):
    def __init__(self, bot, categories, guild_id):
        self.bot = bot
        self.guild_id = guild_id

        options = []
        for category_name, emoji in categories:
            try:
                display_emoji = resolve_component_emoji(DEFAULT_TICKET_EMOJI)
                if emoji and emoji.strip():
                    if emoji.startswith(("<:", "<a:")) and emoji.endswith(">") and ":" in emoji:
                        display_emoji = resolve_component_emoji(emoji)
                    elif len(emoji) <= 4 and not emoji.startswith("<"):
                        display_emoji = resolve_component_emoji(emoji)

                options.append(
                    discord.SelectOption(
                        label=category_name,
                        value=category_name,
                        emoji=display_emoji,
                        description=f"Create a {category_name.lower()} ticket",
                    )
                )
            except Exception as e:
                logger.error(f"Error creating option for category {category_name}: {e}")
                options.append(
                    discord.SelectOption(
                        label=category_name,
                        value=category_name,
                        emoji=resolve_component_emoji(DEFAULT_TICKET_EMOJI),
                        description=f"Create a {category_name.lower()} ticket",
                    )
                )

        super().__init__(placeholder="Select a category", options=options, custom_id="ticket_category_select")

    async def callback(self, interaction: discord.Interaction):
        try:
            logger.info(f"Category select callback triggered by {interaction.user.id} in guild {interaction.guild.id}")

            if not self.values:
                await send_ephemeral_container(
                    interaction, "<:icons_Wrong:1382701332955402341> No category selected. Please try again."
                )
                return

            category = self.values[0]
            logger.info(f"Selected category: {category}")

            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT maintenance_mode FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()
                maintenance_mode = bool(result[0]) if result and result[0] is not None else False

            if maintenance_mode:
                await send_ephemeral_container(
                    interaction,
                    "<:icons_wrench:1382702984940617738> The ticket system is currently under maintenance. Please try again later.",
                )
                return

            if await check_rate_limit(self.bot, interaction.guild.id, interaction.user.id):
                await send_ephemeral_container(
                    interaction,
                    "<:icons_Wrong:1382701332955402341> You're creating tickets too quickly. Please wait 60 seconds before creating another ticket.",
                )
                return

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?",
                    (interaction.guild.id, interaction.user.id),
                )
                if await cur.fetchone():
                    await send_ephemeral_container(
                        interaction,
                        "<:icons_Wrong:1382701332955402341> You are blacklisted from creating tickets in this server.",
                    )
                    return

            open_tickets = await get_user_open_tickets(self.bot, interaction.guild.id, interaction.user.id)

            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT ticket_limit FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()
                ticket_limit = result[0] if result else 3

            if open_tickets >= ticket_limit:
                await send_ephemeral_container(
                    interaction,
                    f"<:Ticket_icons:1382703084815257610> You already have {open_tickets} open tickets. Please close some before creating new ones.",
                )
                return

            confirmation_view = TicketConfirmationView(self.bot, category, interaction.guild.id, interaction.user.id)
            await interaction.response.send_message(view=confirmation_view, ephemeral=True)
            logger.info(f"Confirmation view sent for category {category}")

        except Exception as e:
            import traceback

            error_details = traceback.format_exc()
            logger.error("DETAILED ERROR in category select callback:")
            logger.error(f"Error type: {type(e).__name__}")
            logger.error(f"Error message: {str(e)}")
            logger.error(f"Full traceback: {error_details}")
            logger.error(f"Interaction user: {interaction.user.id}")
            logger.error(f"Guild: {interaction.guild.id}")
            logger.error(f"Selected values: {getattr(self, 'values', 'None')}")

            try:
                await send_ephemeral_container(
                    interaction, f"<:icons_Wrong:1382701332955402341> Error: {type(e).__name__}: {str(e)[:150]}"
                )
            except Exception as follow_error:
                logger.error(f"Failed to send error message: {follow_error}")
                logger.error(f"Follow error traceback: {traceback.format_exc()}")


class TicketButtonPanelView(discord.ui.View):
    def __init__(self, bot, categories, guild_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.categories = categories
        self.guild_id = guild_id

        for idx, (category_name, emoji) in enumerate(categories[:25]):
            self.add_item(TicketCategoryButton(bot, category_name, emoji, idx, guild_id))


class TicketCategoryButton(discord.ui.Button):
    def __init__(self, bot, category, emoji, row, guild_id):
        display_emoji = resolve_component_emoji(DEFAULT_TICKET_EMOJI)
        if emoji and emoji.strip():
            if emoji.startswith(("<:", "<a:")) and emoji.endswith(">") and ":" in emoji:
                display_emoji = resolve_component_emoji(emoji)
            elif len(emoji) <= 4 and not emoji.startswith("<"):
                display_emoji = resolve_component_emoji(emoji)

        super().__init__(
            label=category,
            style=discord.ButtonStyle.primary,
            emoji=display_emoji,
            custom_id=f"ticket_button_{category}",
            row=row // 5,  # 5 buttons per row
        )
        self.bot = bot
        self.category = category
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        try:
            logger.info(f"Category button callback triggered by {interaction.user.id} for category {self.category}")

            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT maintenance_mode FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()
                maintenance_mode = bool(result[0]) if result and result[0] is not None else False

            if maintenance_mode:
                await send_ephemeral_container(
                    interaction,
                    "<:icons_wrench:1382702984940617738> The ticket system is currently under maintenance. Please try again later.",
                )
                return

            if await check_rate_limit(self.bot, interaction.guild.id, interaction.user.id):
                await send_ephemeral_container(
                    interaction,
                    "<:icons_Wrong:1382701332955402341> You're creating tickets too quickly. Please wait 60 seconds before creating another ticket.",
                )
                return

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?",
                    (interaction.guild.id, interaction.user.id),
                )
                if await cur.fetchone():
                    await send_ephemeral_container(
                        interaction,
                        "<:icons_Wrong:1382701332955402341> You are blacklisted from creating tickets in this server.",
                    )
                    return

            open_tickets = await get_user_open_tickets(self.bot, interaction.guild.id, interaction.user.id)

            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT ticket_limit FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()
                ticket_limit = result[0] if result else 3

            if open_tickets >= ticket_limit:
                await send_ephemeral_container(
                    interaction,
                    f"<:Ticket_icons:1382703084815257610> You already have {open_tickets} open tickets. Please close some before creating new ones.",
                )
                return

            confirmation_view = TicketConfirmationView(
                self.bot, self.category, interaction.guild.id, interaction.user.id
            )
            await interaction.response.send_message(view=confirmation_view, ephemeral=True)
            logger.info(f"Confirmation view sent for category {self.category}")

        except Exception as e:
            import traceback

            error_details = traceback.format_exc()
            logger.error("DETAILED ERROR in category button callback:")
            logger.error(f"Error type: {type(e).__name__}")
            logger.error(f"Error message: {str(e)}")
            logger.error(f"Full traceback: {error_details}")
            logger.error(f"Interaction user: {interaction.user.id}")
            logger.error(f"Guild: {interaction.guild.id}")
            logger.error(f"Category: {self.category}")

            try:
                await send_ephemeral_container(
                    interaction, f"<:icons_Wrong:1382701332955402341> Error: {type(e).__name__}: {str(e)[:150]}"
                )
            except Exception as follow_error:
                logger.error(f"Failed to send error message: {follow_error}")
                logger.error(f"Follow error traceback: {traceback.format_exc()}")


class TicketButtonView(discord.ui.View):
    def __init__(self, bot, categories, guild_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.categories = categories
        self.guild_id = guild_id

        for idx, (category_name, emoji) in enumerate(categories):
            if idx >= 25:  # Discord limit
                break
            category_emoji = resolve_component_emoji(emoji) if emoji else None
            self.add_item(TicketCategoryButton(bot, category_name, category_emoji, idx, guild_id))
