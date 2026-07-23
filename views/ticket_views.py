import discord
from discord import ui
import io
import logging
import re
from utils.helpers import check_rate_limit, generate_transcript, send_ephemeral_container
from utils.database import get_user_open_tickets
from utils.config import config
from utils.application_emojis import resolve_component_emoji, resolve_emojis
from utils.theme import PURPLE_HEX, PURPLE_PRIMARY
from views.modals import TicketModal

logger = logging.getLogger("discord")


class PersistentTicketClosedView(discord.ui.View):
    """Persistent view for ticket closed delete/reopen buttons"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, custom_id="ticket_delete_btn")
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            bot = interaction.client
            from utils.database import user_has_support_role

            is_support = await user_has_support_role(bot, interaction.user)
            is_admin = interaction.user.guild_permissions.administrator

            if not (is_support or is_admin):
                await send_ephemeral_container(interaction, "Only staff can delete tickets.")
                return

            await interaction.response.defer()

            async with bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT creator_id, ticket_number, category FROM ticket_instances WHERE channel_id = ?",
                    (interaction.channel.id,),
                )
                ticket_result = await cur.fetchone()

                if not ticket_result:
                    await interaction.followup.send("Ticket not found in database.", ephemeral=True)
                    return

                creator_id, ticket_number, category = ticket_result

            server_name = interaction.guild.name
            _transcript_content, transcript_file = await generate_transcript(interaction.channel)
            transcript_bytes = transcript_file.getvalue()

            if creator_id:
                from utils.tickets import get_ticket_creator_member

                creator = await get_ticket_creator_member(bot, interaction.guild, interaction.channel.id)

                if creator:
                    try:
                        file = discord.File(
                            io.BytesIO(transcript_bytes),
                            filename=f"ticket-{ticket_number:04d}-transcript.txt",
                        )

                        dm_layout = ui.LayoutView()
                        dm_container = ui.Container(accent_color=PURPLE_PRIMARY)
                        dm_container.add_item(
                            ui.TextDisplay(resolve_emojis("# <:clipboard1:1383857546410070117> Ticket Transcript"))
                        )
                        dm_container.add_item(ui.Separator())
                        dm_container.add_item(
                            ui.TextDisplay(
                                f"Your ticket in **{server_name}** has been closed. Here is the complete transcript.\n\n"
                                f"**Category:** {category}"
                            )
                        )
                        dm_container.add_item(ui.File(media=f"attachment://ticket-{ticket_number:04d}-transcript.txt"))
                        dm_layout.add_item(dm_container)

                        await creator.send(view=dm_layout, file=file)
                        logger.info(f"Sent transcript DM to user {creator.id}")

                    except discord.Forbidden:
                        logger.warning(f"Could not send DM to user {creator_id} - DMs disabled")
                    except Exception as dm_error:
                        logger.error(f"Error sending transcript DM: {dm_error}")

            async with bot.db.cursor() as cur:
                await cur.execute("SELECT log_channel_id FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()

                if result and result[0]:
                    log_channel = interaction.guild.get_channel(result[0])
                    if log_channel:
                        delete_time = discord.utils.utcnow()
                        creator_name = "Unknown User"
                        if creator_id:
                            from utils.tickets import get_ticket_creator_member

                            creator = await get_ticket_creator_member(bot, interaction.guild, interaction.channel.id)
                            if creator:
                                creator_name = getattr(creator, "display_name", None) or getattr(
                                    creator, "name", "Unknown User"
                                )

                        log_layout = ui.LayoutView()
                        log_container = ui.Container(accent_color=PURPLE_PRIMARY)
                        log_container.add_item(ui.TextDisplay("### Logs - Ticket Deleted"))
                        log_container.add_item(ui.Separator())
                        log_container.add_item(
                            ui.TextDisplay(
                                f"Ticket `#{ticket_number:04d}` has been deleted {discord.utils.format_dt(delete_time, 'R')}!\n\n"
                                f"**Ticket's Author:** {creator_name} ({creator_id})\n"
                                f"**Deleted By:** {interaction.user.display_name} ({interaction.user.id})\n"
                                f"**Category:** {category}"
                            )
                        )
                        log_container.add_item(ui.Separator())
                        log_container.add_item(ui.File(media=f"attachment://ticket-{ticket_number:04d}-transcript.txt"))
                        log_layout.add_item(log_container)

                        await log_channel.send(
                            view=log_layout,
                            file=discord.File(
                                io.BytesIO(transcript_bytes),
                                filename=f"ticket-{ticket_number:04d}-transcript.txt",
                            ),
                        )

            await interaction.channel.delete(reason=f"Ticket #{ticket_number:04d} deleted by {interaction.user}")

        except Exception as e:
            logger.error(f"Error deleting ticket: {e}")
            try:
                await send_ephemeral_container(interaction, f"Error deleting ticket: {str(e)}")
            except Exception:
                pass

    @discord.ui.button(label="Reopen", style=discord.ButtonStyle.success, custom_id="ticket_reopen_btn")
    async def reopen_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            bot = interaction.client
            from utils.database import user_has_support_role

            is_support = await user_has_support_role(bot, interaction.user)
            is_admin = interaction.user.guild_permissions.administrator

            if not (is_support or is_admin):
                await send_ephemeral_container(interaction, "Only staff can reopen tickets.")
                return

            async with bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT creator_id FROM ticket_instances WHERE channel_id = ?", (interaction.channel.id,)
                )
                result = await cur.fetchone()
                creator_id = result[0] if result else None

            if creator_id:
                creator = interaction.guild.get_member(creator_id)
                if creator:
                    try:
                        await interaction.channel.set_permissions(creator, send_messages=True, view_channel=True)
                        logger.info(f"Restored send_messages permission for user {creator_id} in ticket channel")
                    except Exception as perm_error:
                        logger.warning(f"Could not restore permissions for user: {perm_error}")

            async with bot.db.cursor() as cur:
                await cur.execute(
                    "UPDATE ticket_instances SET status = 'open', closed_at = NULL WHERE channel_id = ?",
                    (interaction.channel.id,),
                )
                await bot.db.commit()

            reopen_layout = ui.LayoutView()
            reopen_container = ui.Container(accent_color=PURPLE_PRIMARY)
            reopen_container.add_item(ui.TextDisplay("### Ticket Reopened"))
            reopen_container.add_item(ui.Separator())
            reopen_container.add_item(ui.TextDisplay(f"Reopened by {interaction.user.mention}"))
            reopen_layout.add_item(reopen_container)

            await interaction.response.send_message(view=reopen_layout)

            await interaction.message.delete()

        except Exception as e:
            logger.error(f"Error reopening ticket: {e}")
            await send_ephemeral_container(interaction, f"Error reopening ticket: {str(e)}")


class TicketConfirmationLayoutView(ui.LayoutView):
    def __init__(self, bot, category, guild_id, user_id):
        super().__init__(timeout=180)
        self.bot = bot
        self.category = category
        self.guild_id = guild_id
        self.user_id = user_id
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

        modal = TicketModal(self.bot, self.category, self.guild_id)
        await interaction.response.send_modal(modal)
        self.stop()

    async def cancel_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await send_ephemeral_container(interaction, "You cannot interact with this menu.")
            return

        await interaction.response.edit_message(view=None, content="Ticket creation cancelled.")
        self.stop()


class TicketPanelLayoutView(ui.LayoutView):
    """Components v2 panel view with dropdown select menu"""

    def __init__(
        self, bot, categories, guild_id, title, description, accent_color, image_url=None, server_icon_url=None
    ):
        super().__init__(timeout=None)
        self.bot = bot
        self.categories = categories
        self.guild_id = guild_id

        self.container = ui.Container(accent_color=accent_color)

        if server_icon_url:
            section = ui.Section(accessory=ui.Thumbnail(media=server_icon_url))
            section.add_item(ui.TextDisplay(resolve_emojis(f"**__{title}__**\n\n{description}")))
            self.container.add_item(section)
        else:
            self.container.add_item(ui.TextDisplay(resolve_emojis(f"**__{title}__**\n\n{description}")))

        self.container.add_item(ui.Separator())

        options = []
        for category_name, emoji in categories[:25]:
            try:
                display_emoji = None
                if emoji and emoji.strip():
                    if emoji.startswith(("<:", "<a:")) and emoji.endswith(">") and ":" in emoji:
                        display_emoji = resolve_component_emoji(emoji)
                    elif len(emoji) <= 4 and not emoji.startswith("<"):
                        display_emoji = resolve_component_emoji(emoji)

                options.append(discord.SelectOption(label=category_name, value=category_name, emoji=display_emoji))
            except Exception as e:
                logger.error(f"Error creating option for category {category_name}: {e}")
                options.append(discord.SelectOption(label=category_name, value=category_name))

        select_row = ui.ActionRow(
            ui.Select(
                placeholder="Select a category to create a ticket...",
                options=options,
                custom_id="ticket_panel_category_select",
            )
        )
        select_row.children[0].callback = self.category_select_callback
        self.container.add_item(select_row)

        if image_url:
            self.container.add_item(ui.Separator())
            gallery = ui.MediaGallery()
            gallery.add_item(media=image_url)
            self.container.add_item(gallery)

        self.add_item(self.container)

    async def category_select_callback(self, interaction: discord.Interaction):
        """Handle category selection from dropdown"""
        try:
            if not interaction.data or "values" not in interaction.data or not interaction.data["values"]:
                await send_ephemeral_container(interaction, "No category selected. Please try again.")
                return

            category = interaction.data["values"][0]

            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT maintenance_mode FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()
                maintenance_mode = bool(result[0]) if result and result[0] is not None else False

            if maintenance_mode:
                await send_ephemeral_container(
                    interaction, "The ticket system is currently under maintenance. Please try again later."
                )
                return

            from utils.helpers import check_rate_limit

            if await check_rate_limit(self.bot, interaction.guild.id, interaction.user.id):
                await send_ephemeral_container(
                    interaction,
                    "You're creating tickets too quickly. Please wait 60 seconds before creating another ticket.",
                )
                return

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?",
                    (interaction.guild.id, interaction.user.id),
                )
                if await cur.fetchone():
                    await send_ephemeral_container(
                        interaction, "You are blacklisted from creating tickets in this server."
                    )
                    return

            from utils.database import get_user_open_tickets

            open_tickets = await get_user_open_tickets(self.bot, interaction.guild.id, interaction.user.id)

            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT ticket_limit FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()
                ticket_limit = result[0] if result else 3

            if open_tickets >= ticket_limit:
                await send_ephemeral_container(
                    interaction,
                    f"You already have {open_tickets} open tickets. Please close some before creating new ones.",
                )
                return

            confirmation_view = TicketConfirmationLayoutView(
                self.bot, category, interaction.guild.id, interaction.user.id
            )
            await interaction.response.send_message(view=confirmation_view, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in panel category select callback: {e}")
            await send_ephemeral_container(interaction, f"An error occurred: {str(e)[:100]}")


class TicketButtonPanelLayoutView(ui.LayoutView):
    """Components v2 panel view with category buttons"""

    def __init__(
        self, bot, categories, guild_id, title, description, accent_color, image_url=None, server_icon_url=None
    ):
        super().__init__(timeout=None)
        self.bot = bot
        self.categories = categories
        self.guild_id = guild_id

        self.container = ui.Container(accent_color=accent_color)

        if server_icon_url:
            section = ui.Section(accessory=ui.Thumbnail(media=server_icon_url))
            section.add_item(ui.TextDisplay(resolve_emojis(f"**__{title}__**\n\n{description}")))
            self.container.add_item(section)
        else:
            self.container.add_item(ui.TextDisplay(resolve_emojis(f"**__{title}__**\n\n{description}")))

        self.container.add_item(ui.Separator())

        buttons = []
        for idx, (category_name, emoji) in enumerate(categories[:25]):
            display_emoji = None
            if emoji and emoji.strip():
                if emoji.startswith(("<:", "<a:")) and emoji.endswith(">") and ":" in emoji:
                    display_emoji = resolve_component_emoji(emoji)
                elif len(emoji) <= 4 and not emoji.startswith("<"):
                    display_emoji = resolve_component_emoji(emoji)

            btn = ui.Button(
                label=category_name,
                style=discord.ButtonStyle.primary,
                emoji=display_emoji,
                custom_id=f"ticket_panel_btn_{category_name}",
            )
            buttons.append((btn, category_name))

        for i in range(0, len(buttons), 5):
            row_buttons = buttons[i : i + 5]
            action_row = ui.ActionRow(*[btn for btn, _ in row_buttons])
            for btn, cat_name in row_buttons:
                btn.callback = self._make_button_callback(cat_name)
            self.container.add_item(action_row)

        if image_url:
            self.container.add_item(ui.Separator())
            gallery = ui.MediaGallery()
            gallery.add_item(media=image_url)
            self.container.add_item(gallery)

        self.add_item(self.container)

    def _make_button_callback(self, category_name):
        async def callback(interaction: discord.Interaction):
            await self._handle_button_click(interaction, category_name)

        return callback

    async def _handle_button_click(self, interaction: discord.Interaction, category: str):
        """Handle category button click"""
        try:
            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT maintenance_mode FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()
                maintenance_mode = bool(result[0]) if result and result[0] is not None else False

            if maintenance_mode:
                await send_ephemeral_container(
                    interaction, "The ticket system is currently under maintenance. Please try again later."
                )
                return

            from utils.helpers import check_rate_limit

            if await check_rate_limit(self.bot, interaction.guild.id, interaction.user.id):
                await send_ephemeral_container(
                    interaction,
                    "You're creating tickets too quickly. Please wait 60 seconds before creating another ticket.",
                )
                return

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?",
                    (interaction.guild.id, interaction.user.id),
                )
                if await cur.fetchone():
                    await send_ephemeral_container(
                        interaction, "You are blacklisted from creating tickets in this server."
                    )
                    return

            from utils.database import get_user_open_tickets

            open_tickets = await get_user_open_tickets(self.bot, interaction.guild.id, interaction.user.id)

            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT ticket_limit FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()
                ticket_limit = result[0] if result else 3

            if open_tickets >= ticket_limit:
                await send_ephemeral_container(
                    interaction,
                    f"You already have {open_tickets} open tickets. Please close some before creating new ones.",
                )
                return

            confirmation_view = TicketConfirmationLayoutView(
                self.bot, category, interaction.guild.id, interaction.user.id
            )
            await interaction.response.send_message(view=confirmation_view, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in panel button callback: {e}")
            await send_ephemeral_container(interaction, f"An error occurred: {str(e)[:100]}")


class PersistentPanelSelect(discord.ui.Select):
    """Persistent select menu for Components v2 panels that works after bot restarts"""

    def __init__(self):
        super().__init__(
            placeholder="Select a category to create a ticket...",
            options=[discord.SelectOption(label="Loading...", value="loading")],
            custom_id="ticket_panel_category_select",
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

            logger.info(f"Persistent panel select callback triggered by {interaction.user.id} for category {category}")

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
                    f"<:icons_Wrong:1382701332955402341> You already have {open_tickets} open tickets. Please close some before creating new ones.",
                )
                return

            confirmation_view = TicketConfirmationLayoutView(bot, category, interaction.guild.id, interaction.user.id)
            await interaction.response.send_message(view=confirmation_view, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in persistent panel select callback: {e}")
            await send_ephemeral_container(interaction, f"An error occurred: {str(e)[:100]}")


class PersistentPanelSelectView(discord.ui.View):
    """Persistent view for Components v2 panel dropdown - register once on startup"""

    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(PersistentPanelSelect())


class PersistentPanelButton(discord.ui.DynamicItem[discord.ui.Button], template=r"ticket_panel_btn_(?P<category>.+)"):
    """Dynamic persistent button for Components v2 panels that works after bot restarts"""

    def __init__(self, category: str):
        super().__init__(
            discord.ui.Button(
                label=category, style=discord.ButtonStyle.primary, custom_id=f"ticket_panel_btn_{category}"
            )
        )
        self.category = category

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Item, match: re.Match[str]):
        category = match["category"]
        return cls(category)

    async def callback(self, interaction: discord.Interaction):
        try:
            bot = interaction.client
            category = self.category

            logger.info(f"Persistent panel button callback triggered by {interaction.user.id} for category {category}")

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
                    f"<:icons_Wrong:1382701332955402341> You already have {open_tickets} open tickets. Please close some before creating new ones.",
                )
                return

            confirmation_view = TicketConfirmationLayoutView(bot, category, interaction.guild.id, interaction.user.id)
            await interaction.response.send_message(view=confirmation_view, ephemeral=True)

        except Exception as e:
            logger.error(f"Error in persistent panel button callback: {e}")
            await send_ephemeral_container(interaction, f"An error occurred: {str(e)[:100]}")


class PersistentPanelButtonView(discord.ui.View):
    """Persistent view for Components v2 panel buttons - register once on startup"""

    def __init__(self):
        super().__init__(timeout=None)


class TicketSetupLayout(ui.LayoutView):
    """Components v2 setup wizard for the ticket system"""

    def __init__(self, bot, ctx):
        super().__init__(timeout=1800)
        self.bot = bot
        self.ctx = ctx
        self.setup_data = {
            "channel_id": None,
            "role_id": None,
            "log_channel_id": None,
            "category_id": None,
            "embed_title": "Support Center",
            "embed_description": "Need assistance? Select a category below to create a support ticket. Our expert team will help you shortly!",
            "embed_color": PURPLE_PRIMARY,
            "embed_image_url": None,
            "ticket_limit": 3,
        }
        self.waiting_for_custom = None
        self.current_step = "main"

        self.container = ui.Container(accent_color=PURPLE_PRIMARY)
        self.setup_main_content()
        self.add_item(self.container)

    def setup_main_content(self):
        """Set up the main setup wizard content"""
        self.container.clear_items()

        self.container.add_item(ui.TextDisplay("# Ticket Setup"))

        self.container.add_item(ui.Separator())

        welcome_text = (
            "Configure your ticket system by selecting the options below.\n\n"
            "**Required:**\n"
            "- Support Role\n"
            "- Panel Channel\n"
            "- Ticket Category\n\n"
            "**Optional:**\n"
            "- Log Channel"
        )
        self.container.add_item(ui.TextDisplay(welcome_text))

        self.container.add_item(ui.Separator())

        self._add_role_select()
        self._add_panel_channel_select()
        self._add_ticket_category_select()
        self._add_log_channel_select()
        self._add_action_buttons()

    def _add_role_select(self):
        """Add support role selection with searchable dropdown"""
        role_select = ui.ActionRow(ui.RoleSelect(placeholder="Select Support Role"))
        role_select.children[0].callback = self.role_select_callback
        self.container.add_item(role_select)

    def _add_panel_channel_select(self):
        """Add panel channel selection with searchable dropdown"""
        channel_select = ui.ActionRow(
            ui.ChannelSelect(placeholder="Select Panel Channel", channel_types=[discord.ChannelType.text])
        )
        channel_select.children[0].callback = self.panel_channel_callback
        self.container.add_item(channel_select)

    def _add_ticket_category_select(self):
        """Add ticket category selection with searchable dropdown"""
        category_select = ui.ActionRow(
            ui.ChannelSelect(
                placeholder="Select Ticket Category (where tickets are created)",
                channel_types=[discord.ChannelType.category],
            )
        )
        category_select.children[0].callback = self.ticket_category_callback
        self.container.add_item(category_select)

    def _add_log_channel_select(self):
        """Add log channel selection with searchable dropdown"""
        log_select = ui.ActionRow(
            ui.ChannelSelect(placeholder="Select Log Channel (Optional)", channel_types=[discord.ChannelType.text])
        )
        log_select.children[0].callback = self.log_channel_callback
        self.container.add_item(log_select)

    def _add_action_buttons(self):
        """Add customization and confirm buttons"""
        button_row = ui.ActionRow(
            ui.Button(label="Customise Panel", style=discord.ButtonStyle.primary),
            ui.Button(label="Confirm Setup", style=discord.ButtonStyle.success),
        )
        button_row.children[0].callback = self.customize_callback
        button_row.children[1].callback = self.confirm_callback
        self.container.add_item(button_row)

    async def role_select_callback(self, interaction: discord.Interaction):
        """Handle support role selection"""
        if interaction.user.id != self.ctx.author.id:
            await send_ephemeral_container(interaction, "You cannot interact with this menu.")
            return

        if interaction.data and "values" in interaction.data and interaction.data["values"]:
            role_id = int(interaction.data["values"][0])
            role = interaction.guild.get_role(role_id)
            self.setup_data["role_id"] = role_id

            await send_ephemeral_container(interaction, f"Support role set to {role.mention}")
        else:
            await send_ephemeral_container(interaction, "Please select a role.")

    async def panel_channel_callback(self, interaction: discord.Interaction):
        """Handle panel channel selection"""
        if interaction.user.id != self.ctx.author.id:
            await send_ephemeral_container(interaction, "You cannot interact with this menu.")
            return

        if interaction.data and "values" in interaction.data and interaction.data["values"]:
            channel_id = int(interaction.data["values"][0])
            channel = interaction.guild.get_channel(channel_id)
            self.setup_data["channel_id"] = channel_id

            await send_ephemeral_container(interaction, f"Panel channel set to {channel.mention}")
        else:
            await send_ephemeral_container(interaction, "Please select a channel.")

    async def ticket_category_callback(self, interaction: discord.Interaction):
        """Handle ticket category selection"""
        if interaction.user.id != self.ctx.author.id:
            await send_ephemeral_container(interaction, "You cannot interact with this menu.")
            return

        if interaction.data and "values" in interaction.data and interaction.data["values"]:
            category_id = int(interaction.data["values"][0])
            category = interaction.guild.get_channel(category_id)
            self.setup_data["category_id"] = category_id

            await send_ephemeral_container(
                interaction,
                f"Ticket category set to **{category.name}**\nAll tickets will be created in this category.",
            )
        else:
            await send_ephemeral_container(interaction, "Please select a category.")

    async def log_channel_callback(self, interaction: discord.Interaction):
        """Handle log channel selection"""
        if interaction.user.id != self.ctx.author.id:
            await send_ephemeral_container(interaction, "You cannot interact with this menu.")
            return

        if interaction.data and "values" in interaction.data and interaction.data["values"]:
            channel_id = int(interaction.data["values"][0])
            channel = interaction.guild.get_channel(channel_id)
            self.setup_data["log_channel_id"] = channel_id

            await send_ephemeral_container(interaction, f"Log channel set to {channel.mention}")
        else:
            await send_ephemeral_container(interaction, "Please select a channel.")

    async def customize_callback(self, interaction: discord.Interaction):
        """Handle customization button click"""
        if interaction.user.id != self.ctx.author.id:
            await send_ephemeral_container(interaction, "You cannot interact with this menu.")
            return

        modal = SetupCustomizationModal(self)
        await interaction.response.send_modal(modal)

    async def confirm_callback(self, interaction: discord.Interaction):
        """Handle confirm button click"""
        if interaction.user.id != self.ctx.author.id:
            await send_ephemeral_container(interaction, "You cannot interact with this menu.")
            return

        if not self.setup_data["channel_id"]:
            await send_ephemeral_container(interaction, "Please select a panel channel first.")
            return

        if not self.setup_data["role_id"]:
            await send_ephemeral_container(interaction, "Please select a support role first.")
            return

        if not self.setup_data["category_id"]:
            await send_ephemeral_container(interaction, "Please select a ticket category first.")
            return

        await self.show_confirmation(interaction)

    async def show_confirmation(self, interaction: discord.Interaction):
        """Show configuration preview before final confirmation"""
        self.container.clear_items()

        guild = interaction.guild
        panel_channel = guild.get_channel(self.setup_data["channel_id"])
        support_role = guild.get_role(self.setup_data["role_id"])
        log_channel = (
            guild.get_channel(self.setup_data["log_channel_id"]) if self.setup_data["log_channel_id"] else None
        )
        ticket_category = guild.get_channel(self.setup_data["category_id"]) if self.setup_data["category_id"] else None

        self.container.add_item(ui.TextDisplay("# Configuration Preview"))

        self.container.add_item(ui.Separator())

        config_text = (
            f"**Panel Channel:** {panel_channel.mention}\n"
            f"**Support Role:** {support_role.mention}\n"
            f"**Ticket Category:** {ticket_category.name if ticket_category else 'Not set'}\n"
            f"**Log Channel:** {log_channel.mention if log_channel else 'Not set'}\n"
            f"**Ticket Limit:** {self.setup_data['ticket_limit']}\n"
            f"**Panel Title:** {self.setup_data['embed_title']}"
        )
        self.container.add_item(ui.TextDisplay(config_text))

        self.container.add_item(ui.Separator())

        final_buttons = ui.ActionRow(
            ui.Button(label="Go Back", style=discord.ButtonStyle.secondary),
            ui.Button(label="Finish Setup", style=discord.ButtonStyle.success),
        )
        final_buttons.children[0].callback = self.go_back_callback
        final_buttons.children[1].callback = self.finish_setup_callback
        self.container.add_item(final_buttons)

        await interaction.response.edit_message(view=self)

    async def go_back_callback(self, interaction: discord.Interaction):
        """Go back to main setup screen"""
        if interaction.user.id != self.ctx.author.id:
            await send_ephemeral_container(interaction, "You cannot interact with this menu.")
            return

        self.setup_main_content()
        await interaction.response.edit_message(view=self)

    async def finish_setup_callback(self, interaction: discord.Interaction):
        """Complete the setup process"""
        if interaction.user.id != self.ctx.author.id:
            await send_ephemeral_container(interaction, "You cannot interact with this menu.")
            return

        try:
            from utils.database import add_or_update_ticket_config

            saved = await add_or_update_ticket_config(
                self.bot,
                self.ctx.guild.id,
                channel_id=self.setup_data["channel_id"],
                role_id=self.setup_data["role_id"],
                category_id=self.setup_data["category_id"],
                log_channel_id=self.setup_data["log_channel_id"],
                embed_title=self.setup_data["embed_title"],
                embed_description=self.setup_data["embed_description"],
                embed_color=self.setup_data["embed_color"],
                embed_image_url=self.setup_data["embed_image_url"],
                ticket_limit=self.setup_data["ticket_limit"],
            )
            if not saved:
                raise RuntimeError("Ticket configuration could not be saved")

            if self.ctx.guild.id in self.bot.active_setups:
                del self.bot.active_setups[self.ctx.guild.id]

            self.container.clear_items()

            self.container.add_item(ui.TextDisplay("# Setup Complete"))

            self.container.add_item(ui.Separator())

            success_text = (
                "Your ticket system has been configured.\n\n"
                "**Next Steps:**\n"
                "- Add categories with `/category add`\n"
                "- Send your panel with `/sendpanel`"
            )
            self.container.add_item(ui.TextDisplay(success_text))

            await interaction.response.edit_message(view=self)
            self.stop()

        except Exception as e:
            logger.error(f"Error finishing setup: {e}")
            await send_ephemeral_container(interaction, f"Setup failed: {str(e)}")

    async def handle_custom_message(self, message):
        """Handle custom input from user messages"""
        if message.author.id != self.ctx.author.id:
            return

        if self.waiting_for_custom == "role":
            if message.role_mentions:
                role = message.role_mentions[0]
                self.setup_data["role_id"] = role.id
                await message.reply(f"Support role set to {role.mention}")
            else:
                await message.reply("Please mention a valid role.")
        elif self.waiting_for_custom == "panel_channel":
            if message.channel_mentions:
                channel = message.channel_mentions[0]
                self.setup_data["channel_id"] = channel.id
                await message.reply(f"Panel channel set to {channel.mention}")
            else:
                await message.reply("Please mention a valid channel.")
        elif self.waiting_for_custom == "log_channel":
            if message.channel_mentions:
                channel = message.channel_mentions[0]
                self.setup_data["log_channel_id"] = channel.id
                await message.reply(f"Log channel set to {channel.mention}")
            else:
                await message.reply("Please mention a valid channel.")

        self.waiting_for_custom = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Check if the user can interact with this view"""
        return interaction.user.id == self.ctx.author.id

    async def on_timeout(self):
        """Handle timeout"""
        if self.ctx.guild.id in self.bot.active_setups:
            del self.bot.active_setups[self.ctx.guild.id]


class SetupCustomizationModal(discord.ui.Modal):
    """Modal for customizing the panel appearance"""

    def __init__(self, setup_view):
        super().__init__(title="Panel Customization")
        self.setup_view = setup_view

    panel_title = discord.ui.TextInput(
        label="Panel Title",
        placeholder="Enter your custom panel title...",
        default="Support Center",
        max_length=100,
        required=True,
    )

    panel_description = discord.ui.TextInput(
        label="Panel Description",
        style=discord.TextStyle.paragraph,
        placeholder="Describe what users should expect...",
        default="Need assistance? Select a category below to create a support ticket.",
        max_length=500,
        required=True,
    )

    panel_color = discord.ui.TextInput(
        label="Accent Color (Hex or 'none')",
        placeholder=f"e.g., {PURPLE_HEX} or none",
        default=PURPLE_HEX,
        max_length=10,
        required=False,
    )

    panel_image = discord.ui.TextInput(
        label="Panel Image URL (Optional)", placeholder="https://example.com/image.png", max_length=200, required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            self.setup_view.setup_data["embed_title"] = self.panel_title.value
            self.setup_view.setup_data["embed_description"] = self.panel_description.value

            if self.panel_image.value.strip():
                image_url = self.panel_image.value.strip()
                if image_url.startswith(("http://", "https://")):
                    self.setup_view.setup_data["embed_image_url"] = image_url
                else:
                    self.setup_view.setup_data["embed_image_url"] = None
            else:
                self.setup_view.setup_data["embed_image_url"] = None

            color_value = self.panel_color.value.strip().lower()

            if color_value == "none" or color_value == "":
                self.setup_view.setup_data["embed_color"] = -1
                color_display = "None"
            else:
                try:
                    if color_value.startswith("#"):
                        color_int = int(color_value[1:], 16)
                    elif color_value.startswith("0x"):
                        color_int = int(color_value, 16)
                    else:
                        color_int = int(color_value, 16)
                    self.setup_view.setup_data["embed_color"] = color_int
                    color_display = f"#{color_int:06X}"
                except (ValueError, AttributeError):
                    self.setup_view.setup_data["embed_color"] = PURPLE_PRIMARY
                    color_display = PURPLE_HEX

            await send_ephemeral_container(
                interaction,
                f"Panel customization saved.\n\n**Title:** {self.panel_title.value}\n**Accent Color:** {color_display}",
            )

        except Exception as e:
            logger.error(f"Error in panel customization modal: {e}")
            await send_ephemeral_container(interaction, f"Error saving customization: {str(e)}")


class TicketControlView(discord.ui.View):
    """Simple backward-compatible ticket control view for persistent button handling"""

    def __init__(self, bot, ticket_data=None):
        super().__init__(timeout=None)
        self.bot = bot
        self.ticket_data = ticket_data or {}

    @discord.ui.button(label="Claim Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_claim_btn")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT role_id FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()

                if not result:
                    await send_ephemeral_container(interaction, "Ticket system is not configured.")
                    return

                from utils.database import user_has_support_role

                has_support_access = await user_has_support_role(self.bot, interaction.user)

                if not has_support_access:
                    await send_ephemeral_container(interaction, "Only support staff can claim tickets.")
                    return

                await cur.execute(
                    "SELECT claimed_by, creator_id FROM ticket_instances WHERE channel_id = ?",
                    (interaction.channel.id,),
                )
                claim_result = await cur.fetchone()

                if not claim_result:
                    await send_ephemeral_container(interaction, "Ticket not found.")
                    return

                claimed_by, creator_id = claim_result

                if claimed_by:
                    if claimed_by == interaction.user.id:
                        await send_ephemeral_container(interaction, "You have already claimed this ticket.")
                        return
                    claimer = interaction.guild.get_member(claimed_by)
                    await send_ephemeral_container(
                        interaction,
                        f"This ticket is already claimed by {claimer.mention if claimer else 'another agent'}.",
                    )
                    return

                await cur.execute(
                    """
                    UPDATE ticket_instances
                    SET claimed_by = ?
                    WHERE channel_id = ? AND claimed_by IS NULL AND status = 'open'
                    """,
                    (interaction.user.id, interaction.channel.id),
                )
                if cur.rowcount == 0:
                    await send_ephemeral_container(
                        interaction,
                        "Another agent claimed this ticket at the same time.",
                    )
                    return
                await self.bot.db.commit()

            ticket_creator = interaction.guild.get_member(creator_id) if creator_id else None
            creator_mention = (
                ticket_creator.mention if ticket_creator else f"<@{creator_id}>" if creator_id else "Ticket Creator"
            )

            await interaction.response.send_message(
                f"{creator_mention} your ticket has been claimed by {interaction.user.mention}", ephemeral=False
            )

        except Exception as e:
            logger.error(f"Error claiming ticket: {e}")
            await send_ephemeral_container(interaction, f"Error claiming ticket: {str(e)}")

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close_btn")
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT creator_id, ticket_number, category FROM ticket_instances WHERE channel_id = ?",
                    (interaction.channel.id,),
                )
                ticket_result = await cur.fetchone()
                if not ticket_result:
                    await send_ephemeral_container(interaction, "Ticket not found.")
                    return
                creator_id, ticket_number, category = ticket_result

            from utils.database import user_has_support_role

            is_creator = creator_id == interaction.user.id
            is_support = await user_has_support_role(self.bot, interaction.user)
            is_admin = interaction.user.guild_permissions.administrator

            if not (is_creator or is_support or is_admin):
                await send_ephemeral_container(interaction, "You don't have permission to close this ticket.")
                return

            await interaction.response.defer()

            ticket_data = {
                "creator_id": creator_id,
                "ticket_number": ticket_number,
                "category": category,
                "subject": "N/A",
            }

            success, message = await close_ticket_channel(self.bot, interaction, ticket_data)
            if not success:
                await interaction.followup.send(f"Error closing ticket: {message}", ephemeral=True)

        except Exception as e:
            logger.error(f"Error in close button: {e}")
            await send_ephemeral_container(interaction, f"Error: {str(e)}")


class TicketControlLayout(ui.LayoutView):
    """Components v2 ticket control panel"""

    def __init__(self, bot, ticket_data, user):
        super().__init__(timeout=None)
        self.bot = bot
        self.ticket_data = ticket_data
        self.user = user

        self.container = ui.Container(accent_color=PURPLE_PRIMARY)
        self._build_layout()
        self.add_item(self.container)

    def _build_layout(self):
        """Build the ticket control panel layout"""
        category = self.ticket_data.get("category", "General")
        self.container.add_item(ui.TextDisplay(f"# {category} Ticket"))

        self.container.add_item(ui.Separator())

        welcome_text = (
            f"**Welcome** {self.user.mention}\n**Category:** {category}\n\nOur support team will assist you shortly."
        )

        welcome_section = ui.Section(accessory=ui.Thumbnail(media=self.user.display_avatar.url))
        welcome_section.add_item(ui.TextDisplay(welcome_text))
        self.container.add_item(welcome_section)

        self.container.add_item(ui.Separator())

        if config.TICKET_BANNER_URL:
            gallery = ui.MediaGallery()
            gallery.add_item(media=config.TICKET_BANNER_URL)
            self.container.add_item(gallery)
            self.container.add_item(ui.Separator())

        button_row = ui.ActionRow(
            ui.Button(label="Claim Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_claim_btn"),
            ui.Button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="ticket_close_btn"),
        )
        button_row.children[0].callback = self.claim_callback
        button_row.children[1].callback = self.close_callback
        self.container.add_item(button_row)

    async def claim_callback(self, interaction: discord.Interaction):
        """Handle claim ticket button"""
        try:
            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT role_id FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()

                if not result:
                    await send_ephemeral_container(interaction, "Ticket system is not configured.")
                    return

                support_role_id = result[0]
                support_role = interaction.guild.get_role(support_role_id)

                from utils.database import user_has_support_role

                has_support_access = await user_has_support_role(self.bot, interaction.user)

                if not has_support_access:
                    await interaction.response.send_message(
                        f"Only support staff can claim tickets.\nRequired role: {support_role.mention if support_role else 'Support Role'}",
                        ephemeral=True,
                    )
                    return

                await cur.execute(
                    "SELECT claimed_by FROM ticket_instances WHERE channel_id = ?", (interaction.channel.id,)
                )
                claim_result = await cur.fetchone()

                if not claim_result:
                    await send_ephemeral_container(interaction, "Ticket not found.")
                    return

                if claim_result[0]:
                    if claim_result[0] == interaction.user.id:
                        await send_ephemeral_container(interaction, "You have already claimed this ticket.")
                        return

                    claimer = interaction.guild.get_member(claim_result[0])
                    await interaction.response.send_message(
                        f"This ticket is already claimed by {claimer.mention if claimer else 'another agent'}.",
                        ephemeral=True,
                    )
                    return

                await cur.execute(
                    """
                    UPDATE ticket_instances
                    SET claimed_by = ?
                    WHERE channel_id = ? AND claimed_by IS NULL AND status = 'open'
                    """,
                    (interaction.user.id, interaction.channel.id),
                )
                if cur.rowcount == 0:
                    await send_ephemeral_container(
                        interaction,
                        "Another agent claimed this ticket at the same time.",
                    )
                    return
                await self.bot.db.commit()

            ticket_creator_id = self.ticket_data.get("creator_id")
            ticket_creator = interaction.guild.get_member(ticket_creator_id)

            if ticket_creator:
                creator_mention = ticket_creator.mention
            elif ticket_creator_id:
                creator_mention = f"<@{ticket_creator_id}>"
            else:
                creator_mention = "Ticket Creator"

            await interaction.response.send_message(
                f"{creator_mention} your ticket has been claimed by {interaction.user.mention}", ephemeral=False
            )

            async def get_ticket_log_channel(bot, guild_id):
                async with bot.db.cursor() as cur:
                    await cur.execute("SELECT log_channel_id FROM tickets WHERE guild_id = ?", (guild_id,))
                    result = await cur.fetchone()
                    return bot.get_channel(result[0]) if result and result[0] else None

            current_time = discord.utils.utcnow()
            log_channel = await get_ticket_log_channel(self.bot, interaction.guild.id)
            if log_channel:
                from utils.tickets import get_ticket_info

                ticket_info = await get_ticket_info(self.bot, interaction.channel.id)

                ticket_number_display = f"#{ticket_info['ticket_number']:04d}" if ticket_info else "#0000"

                claim_log_layout = ui.LayoutView()
                claim_log_container = ui.Container(accent_color=PURPLE_PRIMARY)
                claim_log_container.add_item(ui.TextDisplay("# Logs - Ticket Claimed"))
                claim_log_container.add_item(ui.Separator())
                claim_log_container.add_item(
                    ui.TextDisplay(
                        f"Ticket `{ticket_number_display}` claimed {discord.utils.format_dt(current_time, 'R')}\n\n"
                        f"**Channel:** {interaction.channel.mention}\n"
                        f"**Claimed By:** {interaction.user.display_name}\n"
                        f"**Category:** {ticket_info.get('category', 'Unknown') if ticket_info else 'Unknown'}"
                    )
                )
                claim_log_layout.add_item(claim_log_container)
                await log_channel.send(view=claim_log_layout)

        except Exception as e:
            logger.error(f"Error claiming ticket: {e}")
            await send_ephemeral_container(interaction, f"Error claiming ticket: {str(e)}")

    async def close_callback(self, interaction: discord.Interaction):
        """Handle close ticket button"""
        try:
            from utils.database import user_has_support_role

            is_creator = self.ticket_data.get("creator_id") == interaction.user.id
            is_support = await user_has_support_role(self.bot, interaction.user)
            is_admin = interaction.user.guild_permissions.administrator

            if not (is_creator or is_support or is_admin):
                await send_ephemeral_container(interaction, "You don't have permission to close this ticket.")
                return

            await interaction.response.defer()

            success, message = await close_ticket_channel(self.bot, interaction, self.ticket_data)
            if not success:
                await interaction.followup.send(f"Error closing ticket: {message}", ephemeral=True)

        except Exception as e:
            logger.error(f"Error in close button: {e}")
            await send_ephemeral_container(interaction, f"Error: {str(e)}")


class SetupWizardView(discord.ui.View):
    def __init__(self, bot, guild_id, owner_id):
        super().__init__(timeout=1800)  # 30 minutes timeout
        self.bot = bot
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.setup_data = {
            "channel_id": None,
            "role_id": None,
            "category_id": None,
            "log_channel_id": None,
            "ping_role_id": None,
            "embed_title": " Support Center",
            "embed_description": "Need assistance? Select a category below to create a support ticket. Our expert team will help you shortly!",
            "embed_color": PURPLE_PRIMARY,
            "embed_footer": config.DEFAULT_PANEL_FOOTER,
            "embed_image_url": None,
            "panel_type": "dropdown",
            "ticket_limit": 3,
        }
        self.waiting_for_custom = None  # Track what custom input we're waiting for

        self.add_item(PanelChannelSelect())
        self.add_item(LogChannelSelect())
        self.add_item(SupportRoleSelect())

        self.add_item(CustomRoleButton())
        self.add_item(CustomPanelChannelButton())

        self.add_item(CustomLogChannelButton())
        self.add_item(PanelCustomizationButton())

        self.add_item(ConfirmSetupButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await send_ephemeral_container(interaction, "Only the setup owner can use this wizard.")
        return False

    async def start_setup(self, interaction):
        self.bot.active_setups[self.guild_id] = self

        setup_layout = ui.LayoutView(timeout=1800)
        container = ui.Container(accent_color=PURPLE_PRIMARY)

        container.add_item(ui.TextDisplay(resolve_emojis("# <:icons_wrench:1382702984940617738> Ticket Setup Wizard")))
        container.add_item(ui.Separator())

        content = (
            "**<:clipboard1:1383857546410070117> Required Configuration:**\n"
            "- Select ticket panel channel\n"
            "- Choose logs channel for transcripts\n"
            "- Assign support role for staff\n\n"
            "**<:gear_icons:1384042417975464046> Advanced Options:**\n"
            "- Use custom buttons for unlimited choices\n"
            "- Customize panel appearance and branding\n\n"
            "<:icons_clock:1382701751206936697> **Setup expires in 30 minutes**"
        )
        container.add_item(ui.TextDisplay(resolve_emojis(content)))

        setup_layout.add_item(container)

        await interaction.response.send_message(view=setup_layout, ephemeral=True)
        await interaction.followup.send(view=self, ephemeral=True)

    async def handle_custom_message(self, message):
        """Handle custom input from user messages"""
        if message.author.id != self.owner_id:
            return

        if self.waiting_for_custom == "role":
            if message.role_mentions:
                role = message.role_mentions[0]
                self.setup_data["role_id"] = role.id
                await message.reply(
                    resolve_emojis(f"<:j_icons_Correct:1382701297987485706> Custom support role set to {role.mention}")
                )
            else:
                await message.reply(resolve_emojis("<:icons_Wrong:1382701332955402341> Please mention a valid role!"))
        elif self.waiting_for_custom == "panel_channel":
            if message.channel_mentions:
                channel = message.channel_mentions[0]
                self.setup_data["channel_id"] = channel.id
                await message.reply(
                    resolve_emojis(
                        f"<:j_icons_Correct:1382701297987485706> Custom panel channel set to {channel.mention}"
                    )
                )
            else:
                await message.reply(
                    resolve_emojis("<:icons_Wrong:1382701332955402341> Please mention a valid channel!")
                )
        elif self.waiting_for_custom == "log_channel":
            if message.channel_mentions:
                channel = message.channel_mentions[0]
                self.setup_data["log_channel_id"] = channel.id
                await message.reply(
                    resolve_emojis(
                        f"<:j_icons_Correct:1382701297987485706> Custom log channel set to {channel.mention}"
                    )
                )
            else:
                await message.reply(
                    resolve_emojis("<:icons_Wrong:1382701332955402341> Please mention a valid channel!")
                )

        self.waiting_for_custom = None

    async def finish_setup(self):
        try:
            from utils.database import add_or_update_ticket_config

            saved = await add_or_update_ticket_config(
                self.bot,
                self.guild_id,
                channel_id=self.setup_data["channel_id"],
                role_id=self.setup_data["role_id"],
                category_id=self.setup_data["category_id"],
                log_channel_id=self.setup_data["log_channel_id"],
                ping_role_id=self.setup_data["ping_role_id"],
                embed_title=self.setup_data["embed_title"],
                embed_description=self.setup_data["embed_description"],
                embed_color=self.setup_data["embed_color"],
                embed_footer=self.setup_data["embed_footer"],
                embed_image_url=self.setup_data["embed_image_url"],
                panel_type=self.setup_data["panel_type"],
                ticket_limit=self.setup_data["ticket_limit"],
            )
            if not saved:
                raise RuntimeError("Ticket configuration could not be saved")

            if self.guild_id in self.bot.active_setups:
                del self.bot.active_setups[self.guild_id]

            return True, "<:j_icons_Correct:1382701297987485706> Setup completed successfully!"

        except Exception as e:
            logger.error(f"Error finishing setup: {e}")
            return False, f"<:icons_Wrong:1382701332955402341> Setup failed: {str(e)}"


class PanelChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder=resolve_emojis("<:Ticket_icons:1382703084815257610> Select Ticket Panel Channel..."),
            channel_types=[discord.ChannelType.text],
            custom_id="panel_channel_select",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        view: SetupWizardView = self.view
        view.setup_data["channel_id"] = self.values[0].id

        await send_ephemeral_container(
            interaction,
            f"# Panel Channel Selected\n\n**Ticket Panel Channel:** {self.values[0].mention}\n\nUsers will create tickets from this channel.",
        )


class LogChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder=resolve_emojis("<:clipboard1:1383857546410070117> Select Logs Channel..."),
            channel_types=[discord.ChannelType.text],
            custom_id="log_channel_select",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        view: SetupWizardView = self.view
        view.setup_data["log_channel_id"] = self.values[0].id

        await send_ephemeral_container(
            interaction,
            f"# Log Channel Selected\n\n**Log Channel:** {self.values[0].mention}\n\nTicket transcripts and logs will be sent here.",
        )


class SupportRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder="Select Support Role...", custom_id="support_role_select", row=0)

    async def callback(self, interaction: discord.Interaction):
        view: SetupWizardView = self.view
        view.setup_data["role_id"] = self.values[0].id

        await send_ephemeral_container(
            interaction,
            f"# Support Role Selected\n\n**Support Role:** {self.values[0].mention}\n\nMembers with this role can manage tickets.",
        )


class CustomRoleButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Custom Role",
            style=discord.ButtonStyle.secondary,
            emoji=resolve_component_emoji("<:shield:1382703287891136564>"),
            custom_id="custom_role_btn",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        view: SetupWizardView = self.view
        view.waiting_for_custom = "role"

        await send_ephemeral_container(
            interaction, "# Custom Support Role\n\n**Please mention the role in chat**\n\nExample: `@Support Team`"
        )


class CustomPanelChannelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Custom Panel Channel",
            style=discord.ButtonStyle.secondary,
            emoji=resolve_component_emoji("<:megaphone:1382704888294936649>"),
            custom_id="custom_panel_channel_btn",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        view: SetupWizardView = self.view
        view.waiting_for_custom = "panel_channel"

        await send_ephemeral_container(
            interaction, "# Custom Panel Channel\n\n**Please mention the channel in chat**\n\nExample: `#support`"
        )


class CustomLogChannelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Custom Log Channel",
            style=discord.ButtonStyle.secondary,
            emoji=resolve_component_emoji("<:stats_1:1382703019334045830>"),
            custom_id="custom_log_channel_btn",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        view: SetupWizardView = self.view
        view.waiting_for_custom = "log_channel"

        await send_ephemeral_container(
            interaction, "# Custom Log Channel\n\n**Please mention the channel in chat**\n\nExample: `#ticket-logs`"
        )


class PanelCustomizationButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Panel Customization",
            style=discord.ButtonStyle.primary,
            emoji=resolve_component_emoji("<:paint_icons:1383849816022581332>"),
            custom_id="panel_customization_btn",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction):
        modal = NewPanelCustomizationModal(self.view)
        await interaction.response.send_modal(modal)


class ConfirmSetupButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Confirm Setup",
            style=discord.ButtonStyle.success,
            emoji=resolve_component_emoji("<:j_icons_Correct:1382701297987485706>"),
            custom_id="confirm_setup_btn",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        view: SetupWizardView = self.view

        if not view.setup_data["channel_id"]:
            await send_ephemeral_container(
                interaction, "# Missing Panel Channel\n\nPlease select a ticket panel channel first!"
            )
            return

        if not view.setup_data["role_id"]:
            await send_ephemeral_container(interaction, "# Missing Support Role\n\nPlease select a support role first!")
            return

        guild = interaction.guild
        panel_channel = guild.get_channel(view.setup_data["channel_id"])
        support_role = guild.get_role(view.setup_data["role_id"])
        log_channel = (
            guild.get_channel(view.setup_data["log_channel_id"]) if view.setup_data["log_channel_id"] else None
        )

        confirm_view = FinalConfirmView(view)

        preview_layout = ui.LayoutView(timeout=300)
        container = ui.Container(accent_color=PURPLE_PRIMARY)

        container.add_item(ui.TextDisplay("# Configuration Preview"))
        container.add_item(ui.Separator())

        config_text = (
            f"**Panel Channel:** {panel_channel.mention}\n"
            f"**Support Role:** {support_role.mention}\n"
            f"**Log Channel:** {log_channel.mention if log_channel else 'None'}\n"
            f"**Panel Title:** {view.setup_data['embed_title']}"
        )
        container.add_item(ui.TextDisplay(config_text))

        container.add_item(ui.Separator())

        button_row = ui.ActionRow(
            ui.Button(label="Finish Setup", style=discord.ButtonStyle.success, custom_id="finish_setup_btn"),
            ui.Button(label="Cancel", style=discord.ButtonStyle.danger, custom_id="cancel_setup_btn"),
        )
        button_row.children[0].callback = confirm_view.finish_setup_callback
        button_row.children[1].callback = confirm_view.cancel_setup_callback
        container.add_item(button_row)

        preview_layout.add_item(container)
        confirm_view.preview_layout = preview_layout
        confirm_view.container = container

        await interaction.response.send_message(view=preview_layout, ephemeral=True)


class FinalConfirmView(discord.ui.View):
    def __init__(self, setup_view):
        super().__init__(timeout=300)
        self.setup_view = setup_view
        self.preview_layout = None
        self.container = None

    async def finish_setup_callback(self, interaction: discord.Interaction):
        success, message = await self.setup_view.finish_setup()

        if self.container:
            self.container.clear_items()

            if success:
                self.container.add_item(ui.TextDisplay("# Setup Complete!"))
                self.container.add_item(ui.Separator())
                self.container.add_item(
                    ui.TextDisplay(
                        "**Your ticket system is ready!**\n\n"
                        "**Next Steps:**\n"
                        "- Add categories: `/category add <name>`\n"
                        "- Send panel: `/sendpanel dropdown`\n"
                        "- Test the system: Create a ticket!"
                    )
                )
            else:
                self.container.add_item(ui.TextDisplay("# Setup Failed"))
                self.container.add_item(ui.Separator())
                self.container.add_item(ui.TextDisplay(f"**Error:** {message}\n\nPlease try the setup again."))

            await interaction.response.edit_message(view=self.preview_layout)

    async def cancel_setup_callback(self, interaction: discord.Interaction):
        if self.container:
            self.container.clear_items()
            self.container.add_item(ui.TextDisplay("# Setup Cancelled"))
            self.container.add_item(ui.Separator())
            self.container.add_item(
                ui.TextDisplay(
                    "Setup has been cancelled. No changes were made.\n\nYou can restart setup anytime with `/setup`."
                )
            )

            await interaction.response.edit_message(view=self.preview_layout)


class TicketSetupView(discord.ui.View):
    def __init__(self, bot, ctx):
        super().__init__(timeout=1800)  # 30 minutes timeout
        self.bot = bot
        self.ctx = ctx
        self.setup_data = {
            "channel_id": None,
            "role_id": None,
            "log_channel_id": None,
            "embed_title": "Support Center",
            "embed_description": "Need assistance? Select a category below to create a support ticket. Our expert team will help you shortly!",
            "embed_color": PURPLE_PRIMARY,
            "embed_image_url": None,
            "embed_footer": config.DEFAULT_PANEL_FOOTER,
            "ticket_limit": 3,
        }
        self.waiting_for_custom = None

        self.add_item(SetupSupportRoleSelect(ctx.guild))

        self.add_item(SetupPanelChannelSelect(ctx.guild))

        self.add_item(SetupLogChannelSelect(ctx.guild))

        self.add_item(SetupPanelCustomizationButton())
        self.add_item(SetupConfirmButton())

    async def handle_custom_message(self, message):
        """Handle custom input from user messages"""
        if message.author.id != self.ctx.author.id:
            return

        if self.waiting_for_custom == "role":
            if message.role_mentions:
                role = message.role_mentions[0]
                self.setup_data["role_id"] = role.id
                await message.reply(
                    resolve_emojis(
                        f"<:j_icons_Correct:1382701297987485706> **Custom support role set to {role.mention}**"
                    )
                )
            else:
                await message.reply(resolve_emojis("<:icons_Wrong:1382701332955402341> Please mention a valid role!"))
        elif self.waiting_for_custom == "panel_channel":
            if message.channel_mentions:
                channel = message.channel_mentions[0]
                self.setup_data["channel_id"] = channel.id
                await message.reply(
                    resolve_emojis(
                        f"<:j_icons_Correct:1382701297987485706> **Custom panel channel set to {channel.mention}**"
                    )
                )
            else:
                await message.reply(
                    resolve_emojis("<:icons_Wrong:1382701332955402341> Please mention a valid channel!")
                )
        elif self.waiting_for_custom == "log_channel":
            if message.channel_mentions:
                channel = message.channel_mentions[0]
                self.setup_data["log_channel_id"] = channel.id
                await message.reply(
                    resolve_emojis(
                        f"<:j_icons_Correct:1382701297987485706> **Custom log channel set to {channel.mention}**"
                    )
                )
            else:
                await message.reply(
                    resolve_emojis("<:icons_Wrong:1382701332955402341> Please mention a valid channel!")
                )

        self.waiting_for_custom = None

    async def finish_setup(self):
        try:
            from utils.database import add_or_update_ticket_config

            saved = await add_or_update_ticket_config(
                self.bot,
                self.ctx.guild.id,
                channel_id=self.setup_data["channel_id"],
                role_id=self.setup_data["role_id"],
                log_channel_id=self.setup_data["log_channel_id"],
                embed_title=self.setup_data["embed_title"],
                embed_description=self.setup_data["embed_description"],
                embed_color=self.setup_data["embed_color"],
                embed_image_url=self.setup_data["embed_image_url"],
                embed_footer=self.setup_data["embed_footer"],
                ticket_limit=self.setup_data["ticket_limit"],
            )
            if not saved:
                raise RuntimeError("Ticket configuration could not be saved")

            if self.ctx.guild.id in self.bot.active_setups:
                del self.bot.active_setups[self.ctx.guild.id]

            return True, "<:j_icons_Correct:1382701297987485706> Setup completed successfully!"

        except Exception as e:
            logger.error(f"Error finishing setup: {e}")
            return False, f"<:icons_Wrong:1382701332955402341> Setup failed: {str(e)}"


class SetupPanelChannelSelect(discord.ui.Select):
    def __init__(self, guild):
        text_channels = [ch for ch in guild.channels if isinstance(ch, discord.TextChannel)]

        options = []
        for channel in text_channels[:24]:
            options.append(
                discord.SelectOption(label=f"#{channel.name}", value=str(channel.id), description=f"ID: {channel.id}")
            )

        if len(text_channels) > 24:
            options.append(
                discord.SelectOption(
                    label="Custom Channel",
                    value="custom_channel",
                    description="Mention a channel in chat",
                    emoji=resolve_component_emoji("<:megaphone:1382704888294936649>"),
                )
            )

        super().__init__(
            placeholder="Select Panel Channel...", options=options, custom_id="setup_panel_channel_select", row=1
        )

    async def callback(self, interaction: discord.Interaction):
        view: TicketSetupView = self.view

        if self.values[0] == "custom_channel":
            view.waiting_for_custom = "panel_channel"
            await send_ephemeral_container(
                interaction, "# Custom Panel Channel\n\n**Please mention the channel in chat**\n\nExample: `#support`"
            )
        else:
            channel_id = int(self.values[0])
            channel = interaction.guild.get_channel(channel_id)
            view.setup_data["channel_id"] = channel_id
            await send_ephemeral_container(
                interaction,
                f"# Panel Channel Selected\n\n**Ticket Panel Channel:** {channel.mention}\n\nUsers will create tickets from this channel.",
            )


class SetupLogChannelSelect(discord.ui.Select):
    def __init__(self, guild):
        text_channels = [ch for ch in guild.channels if isinstance(ch, discord.TextChannel)]

        options = []
        for channel in text_channels[:24]:
            options.append(
                discord.SelectOption(label=f"#{channel.name}", value=str(channel.id), description=f"ID: {channel.id}")
            )

        if len(text_channels) > 24:
            options.append(
                discord.SelectOption(
                    label="Custom Log Channel",
                    value="custom_log_channel",
                    description="Mention a channel in chat",
                    emoji=resolve_component_emoji("<:stats_1:1382703019334045830>"),
                )
            )

        super().__init__(
            placeholder="Select Log Channel...", options=options, custom_id="setup_log_channel_select", row=2
        )

    async def callback(self, interaction: discord.Interaction):
        view: TicketSetupView = self.view

        if self.values[0] == "custom_log_channel":
            view.waiting_for_custom = "log_channel"
            await send_ephemeral_container(
                interaction, "# Custom Log Channel\n\n**Please mention the channel in chat**\n\nExample: `#ticket-logs`"
            )
        else:
            channel_id = int(self.values[0])
            channel = interaction.guild.get_channel(channel_id)
            view.setup_data["log_channel_id"] = channel_id
            await send_ephemeral_container(
                interaction,
                f"# Log Channel Selected\n\n**Log Channel:** {channel.mention}\n\nTicket transcripts and logs will be sent here.",
            )


class SetupSupportRoleSelect(discord.ui.Select):
    def __init__(self, guild):
        roles = [role for role in guild.roles if role != guild.default_role]

        options = []
        for role in roles[:24]:
            options.append(
                discord.SelectOption(label=f"@{role.name}", value=str(role.id), description=f"ID: {role.id}")
            )

        if len(roles) > 24:
            options.append(
                discord.SelectOption(
                    label="Custom Role",
                    value="custom_role",
                    description="Mention a role in chat",
                    emoji=resolve_component_emoji("<:shield:1382703287891136564>"),
                )
            )

        super().__init__(
            placeholder="Select Support Role...", options=options, custom_id="setup_support_role_select", row=0
        )

    async def callback(self, interaction: discord.Interaction):
        view: TicketSetupView = self.view

        if self.values[0] == "custom_role":
            view.waiting_for_custom = "role"
            await send_ephemeral_container(
                interaction, "# Custom Support Role\n\n**Please mention the role in chat**\n\nExample: `@Support Team`"
            )
        else:
            role_id = int(self.values[0])
            role = interaction.guild.get_role(role_id)
            view.setup_data["role_id"] = role_id
            await send_ephemeral_container(
                interaction,
                f"# Support Role Selected\n\n**Support Role:** {role.mention}\n\nMembers with this role can manage tickets.",
            )


class NewPanelCustomizationModal(discord.ui.Modal):
    def __init__(self, setup_view):
        super().__init__(title="Panel Customization")
        self.setup_view = setup_view

    panel_title = discord.ui.TextInput(
        label="Panel Title",
        placeholder="Enter your custom panel title...",
        default="Support Center",
        max_length=100,
        required=True,
    )

    panel_description = discord.ui.TextInput(
        label="Panel Description",
        style=discord.TextStyle.paragraph,
        placeholder="Describe what users should expect...",
        default="Need assistance? Select a category below to create a support ticket. Our expert team will help you shortly!",
        max_length=500,
        required=True,
    )

    panel_color = discord.ui.TextInput(
        label="Panel Color (Hex Code)",
        placeholder=f"e.g., {PURPLE_HEX} or 0x8B5CF6",
        default=PURPLE_HEX,
        max_length=10,
        required=False,
    )

    panel_footer = discord.ui.TextInput(
        label="Panel Footer Text",
        placeholder="Footer text for your panel...",
        default=config.DEFAULT_PANEL_FOOTER,
        max_length=100,
        required=False,
    )

    panel_image = discord.ui.TextInput(
        label="Panel Image URL (Optional)", placeholder="https://example.com/image.png", max_length=200, required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)

            self.setup_view.setup_data["embed_title"] = self.panel_title.value
            self.setup_view.setup_data["embed_description"] = self.panel_description.value
            self.setup_view.setup_data["embed_footer"] = self.panel_footer.value

            if self.panel_image.value.strip():
                image_url = self.panel_image.value.strip()
                if image_url.startswith(("http://", "https://")):
                    self.setup_view.setup_data["embed_image_url"] = image_url
                else:
                    self.setup_view.setup_data["embed_image_url"] = None
            else:
                self.setup_view.setup_data["embed_image_url"] = None

            color_value = self.panel_color.value.strip()
            try:
                if color_value.startswith("#"):
                    color_int = int(color_value[1:], 16)
                elif color_value.startswith("0x"):
                    color_int = int(color_value, 16)
                else:
                    color_int = int(color_value, 16)
                self.setup_view.setup_data["embed_color"] = color_int
            except (ValueError, AttributeError):
                self.setup_view.setup_data["embed_color"] = PURPLE_PRIMARY

            preview_text = (
                f"**Title:** {self.panel_title.value}\n"
                f"**Color:** #{hex(self.setup_view.setup_data['embed_color'])[2:].upper()}\n"
                f"**Image:** {'Set' if self.setup_view.setup_data.get('embed_image_url') else 'None'}"
            )
            await send_ephemeral_container(
                interaction,
                f"# Panel Customization Saved\n\nYour panel customization has been applied!\n\n{preview_text}",
            )

        except Exception as e:
            logger.error(f"Error in panel customization modal: {e}")
            await send_ephemeral_container(interaction, f"# Customization Error\n\n**Failed to save:** {str(e)}")


class SetupPanelCustomizationButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Customise Panel",
            style=discord.ButtonStyle.primary,
            emoji=resolve_component_emoji("<:paint_icons:1383849816022581332>"),
            custom_id="setup_panel_customization_btn",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        modal = NewPanelCustomizationModal(self.view)
        await interaction.response.send_modal(modal)


class SetupConfirmButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Confirm",
            style=discord.ButtonStyle.success,
            emoji=resolve_component_emoji("<:j_icons_Correct:1382701297987485706>"),
            custom_id="setup_confirm_btn",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction):
        view: TicketSetupView = self.view

        if not view.setup_data["channel_id"]:
            await send_ephemeral_container(
                interaction, "# Missing Panel Channel\n\nPlease select a ticket panel channel first!"
            )
            return

        if not view.setup_data["role_id"]:
            await send_ephemeral_container(interaction, "# Missing Support Role\n\nPlease select a support role first!")
            return

        guild = interaction.guild
        panel_channel = guild.get_channel(view.setup_data["channel_id"])
        support_role = guild.get_role(view.setup_data["role_id"])
        log_channel = (
            guild.get_channel(view.setup_data["log_channel_id"]) if view.setup_data["log_channel_id"] else None
        )

        confirm_view = SetupFinalConfirmView(view)

        preview_layout = ui.LayoutView(timeout=300)
        container = ui.Container(accent_color=PURPLE_PRIMARY)

        container.add_item(ui.TextDisplay("# Configuration Preview"))
        container.add_item(ui.Separator())

        config_text = (
            f"**Panel Channel:** {panel_channel.mention}\n"
            f"**Support Role:** {support_role.mention}\n"
            f"**Log Channel:** {log_channel.mention if log_channel else 'None'}\n"
            f"**Panel Title:** {view.setup_data['embed_title']}"
        )
        container.add_item(ui.TextDisplay(config_text))

        container.add_item(ui.Separator())

        button_row = ui.ActionRow(
            ui.Button(label="Finish Setup", style=discord.ButtonStyle.success, custom_id="setup_finish_btn"),
            ui.Button(label="Cancel", style=discord.ButtonStyle.danger, custom_id="setup_cancel_btn"),
        )
        button_row.children[0].callback = confirm_view.finish_setup_callback
        button_row.children[1].callback = confirm_view.cancel_setup_callback
        container.add_item(button_row)

        preview_layout.add_item(container)
        confirm_view.preview_layout = preview_layout
        confirm_view.container = container

        await interaction.response.send_message(view=preview_layout, ephemeral=True)


class SetupFinalConfirmView(discord.ui.View):
    def __init__(self, setup_view):
        super().__init__(timeout=300)
        self.setup_view = setup_view
        self.preview_layout = None
        self.container = None

    async def finish_setup_callback(self, interaction: discord.Interaction):
        success, message = await self.setup_view.finish_setup()

        if self.container:
            self.container.clear_items()

            if success:
                self.container.add_item(ui.TextDisplay("# Setup Complete!"))
                self.container.add_item(ui.Separator())
                self.container.add_item(
                    ui.TextDisplay(
                        "**Your ticket system is ready!**\n\n"
                        "**Next Steps:**\n"
                        "- Add categories: `/category add <name>`\n"
                        "- Send panel: `/sendpanel dropdown`\n"
                        "- Test the system: Create a ticket!"
                    )
                )
            else:
                self.container.add_item(ui.TextDisplay("# Setup Failed"))
                self.container.add_item(ui.Separator())
                self.container.add_item(ui.TextDisplay(f"**Error:** {message}\n\nPlease try the setup again."))

            await interaction.response.edit_message(view=self.preview_layout)

    async def cancel_setup_callback(self, interaction: discord.Interaction):
        if self.container:
            self.container.clear_items()
            self.container.add_item(ui.TextDisplay("# Setup Cancelled"))
            self.container.add_item(ui.Separator())
            self.container.add_item(
                ui.TextDisplay(
                    "Setup has been cancelled. No changes were made.\n\nYou can restart setup anytime with `/setup`."
                )
            )

            await interaction.response.edit_message(view=self.preview_layout)


class TicketClosedLayout(ui.LayoutView):
    """Layout shown when a ticket is closed with Delete/Reopen buttons"""

    def __init__(self, bot, ticket_data):
        super().__init__(timeout=None)
        self.bot = bot
        self.ticket_data = ticket_data

        self.container = ui.Container(accent_color=PURPLE_PRIMARY)
        self._build_layout()
        self.add_item(self.container)

    def _build_layout(self):
        self.container.add_item(ui.TextDisplay("### Ticket Closed"))
        self.container.add_item(ui.Separator())

        button_row = ui.ActionRow(
            ui.Button(label="Delete", style=discord.ButtonStyle.danger, custom_id="ticket_delete_btn"),
            ui.Button(label="Reopen", style=discord.ButtonStyle.success, custom_id="ticket_reopen_btn"),
        )
        button_row.children[0].callback = self.delete_callback
        button_row.children[1].callback = self.reopen_callback
        self.container.add_item(button_row)

    async def delete_callback(self, interaction: discord.Interaction):
        try:
            from utils.database import user_has_support_role

            is_support = await user_has_support_role(self.bot, interaction.user)
            is_admin = interaction.user.guild_permissions.administrator

            if not (is_support or is_admin):
                await send_ephemeral_container(interaction, "Only staff can delete tickets.")
                return

            await interaction.response.defer()

            ticket_number = self.ticket_data.get("ticket_number", 0)
            creator_id = self.ticket_data.get("creator_id")
            category = self.ticket_data.get("category", "Unknown")
            server_name = interaction.guild.name

            _transcript_content, transcript_file = await generate_transcript(interaction.channel)
            transcript_bytes = transcript_file.getvalue()

            if creator_id:
                from utils.tickets import get_ticket_creator_member

                creator = await get_ticket_creator_member(self.bot, interaction.guild, interaction.channel.id)

                if creator:
                    try:
                        file = discord.File(
                            io.BytesIO(transcript_bytes),
                            filename=f"ticket-{ticket_number:04d}-transcript.txt",
                        )

                        dm_layout = ui.LayoutView()
                        dm_container = ui.Container(accent_color=PURPLE_PRIMARY)
                        dm_container.add_item(
                            ui.TextDisplay(resolve_emojis("# <:clipboard1:1383857546410070117> Ticket Transcript"))
                        )
                        dm_container.add_item(ui.Separator())
                        dm_container.add_item(
                            ui.TextDisplay(
                                f"Your ticket in **{server_name}** has been closed. Here is the complete transcript.\n\n"
                                f"**Category:** {category}"
                            )
                        )
                        dm_container.add_item(ui.File(media=f"attachment://ticket-{ticket_number:04d}-transcript.txt"))
                        dm_layout.add_item(dm_container)

                        await creator.send(view=dm_layout, file=file)
                        logger.info(f"Sent transcript DM to user {creator.id}")

                    except discord.Forbidden:
                        logger.warning(f"Could not send DM to user {creator_id} - DMs disabled")
                    except Exception as dm_error:
                        logger.error(f"Error sending transcript DM: {dm_error}")

            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT log_channel_id FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
                result = await cur.fetchone()

                if result and result[0]:
                    log_channel = interaction.guild.get_channel(result[0])
                    if log_channel:
                        delete_time = discord.utils.utcnow()
                        creator_name = "Unknown User"
                        if creator_id:
                            from utils.tickets import get_ticket_creator_member

                            creator_member = await get_ticket_creator_member(
                                self.bot, interaction.guild, interaction.channel.id
                            )
                            if creator_member:
                                creator_name = getattr(creator_member, "display_name", None) or getattr(
                                    creator_member, "name", "Unknown User"
                                )

                        log_layout = ui.LayoutView()
                        log_container = ui.Container(accent_color=PURPLE_PRIMARY)
                        log_container.add_item(ui.TextDisplay("### Logs - Ticket Deleted"))
                        log_container.add_item(ui.Separator())
                        log_container.add_item(
                            ui.TextDisplay(
                                f"Ticket `#{ticket_number:04d}` has been deleted {discord.utils.format_dt(delete_time, 'R')}!\n\n"
                                f"**Ticket's Author:** {creator_name} ({creator_id})\n"
                                f"**Deleted By:** {interaction.user.display_name} ({interaction.user.id})\n"
                                f"**Category:** {category}"
                            )
                        )
                        log_container.add_item(ui.Separator())
                        log_container.add_item(ui.File(media=f"attachment://ticket-{ticket_number:04d}-transcript.txt"))
                        log_layout.add_item(log_container)

                        await log_channel.send(
                            view=log_layout,
                            file=discord.File(
                                io.BytesIO(transcript_bytes),
                                filename=f"ticket-{ticket_number:04d}-transcript.txt",
                            ),
                        )

            await interaction.channel.delete(reason=f"Ticket #{ticket_number:04d} deleted by {interaction.user}")

        except Exception as e:
            logger.error(f"Error deleting ticket: {e}")
            await send_ephemeral_container(interaction, f"Error deleting ticket: {str(e)}")

    async def reopen_callback(self, interaction: discord.Interaction):
        try:
            from utils.database import user_has_support_role

            is_support = await user_has_support_role(self.bot, interaction.user)
            is_admin = interaction.user.guild_permissions.administrator

            if not (is_support or is_admin):
                await send_ephemeral_container(interaction, "Only staff can reopen tickets.")
                return

            creator_id = self.ticket_data.get("creator_id")
            if creator_id:
                creator = interaction.guild.get_member(creator_id)
                if creator:
                    try:
                        await interaction.channel.set_permissions(creator, send_messages=True, view_channel=True)
                        logger.info(f"Restored send_messages permission for user {creator_id} in ticket channel")
                    except Exception as perm_error:
                        logger.warning(f"Could not restore permissions for user: {perm_error}")

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "UPDATE ticket_instances SET status = 'open', closed_at = NULL WHERE channel_id = ?",
                    (interaction.channel.id,),
                )
                await self.bot.db.commit()

            reopen_layout = ui.LayoutView()
            reopen_container = ui.Container(accent_color=PURPLE_PRIMARY)
            reopen_container.add_item(ui.TextDisplay("### Ticket Reopened"))
            reopen_container.add_item(ui.Separator())
            reopen_container.add_item(ui.TextDisplay(f"Reopened by {interaction.user.mention}"))
            reopen_layout.add_item(reopen_container)

            await interaction.response.send_message(view=reopen_layout)

            await interaction.message.delete()

        except Exception as e:
            logger.error(f"Error reopening ticket: {e}")
            await send_ephemeral_container(interaction, f"Error reopening ticket: {str(e)}")


async def close_ticket_channel(bot, interaction, ticket_data: dict):
    """Close a ticket channel - removes author permissions, logs, updates DB, and shows closed panel"""
    try:
        channel = interaction.channel
        creator_id = ticket_data.get("creator_id")

        from utils.tickets import get_ticket_creator_member

        creator = await get_ticket_creator_member(bot, interaction.guild, channel.id) if creator_id else None

        ticket_number = ticket_data.get("ticket_number", 0)

        async with bot.db.cursor() as cur:
            await cur.execute(
                """
                UPDATE ticket_instances
                SET status = 'closed', closed_at = CURRENT_TIMESTAMP
                WHERE channel_id = ? AND status = 'open'
                """,
                (channel.id,),
            )
            if cur.rowcount == 0:
                return False, "Ticket is already closed or no longer exists."
            await bot.db.commit()

        if creator and hasattr(creator, "id"):
            try:
                await channel.set_permissions(creator, send_messages=False)
                logger.info(f"Removed send_messages permission for user {creator.id} in ticket channel")
            except Exception as perm_error:
                logger.warning(f"Could not update permissions for user: {perm_error}")

        async with bot.db.cursor() as cur:
            await cur.execute("SELECT log_channel_id FROM tickets WHERE guild_id = ?", (interaction.guild.id,))
            result = await cur.fetchone()

            if result and result[0]:
                log_channel = interaction.guild.get_channel(result[0])
                if log_channel:
                    close_time = discord.utils.utcnow()

                    creator_name = "Unknown User"
                    if creator:
                        creator_name = getattr(creator, "display_name", None) or getattr(
                            creator, "name", "Unknown User"
                        )

                    from utils.author_info import TicketClosedLogView

                    log_view = TicketClosedLogView(bot, ticket_data)

                    log_layout = ui.LayoutView()
                    log_container = ui.Container(accent_color=PURPLE_PRIMARY)
                    log_container.add_item(ui.TextDisplay("### Logs - Ticket Closed"))
                    log_container.add_item(ui.Separator())
                    log_container.add_item(
                        ui.TextDisplay(
                            f"Ticket `#{ticket_number:04d}` has been closed {discord.utils.format_dt(close_time, 'R')}!\n\n"
                            f"**Ticket's Author:** {creator_name} ({ticket_data.get('creator_id')})\n"
                            f"**Closed By:** {interaction.user.display_name} ({interaction.user.id})\n"
                            f"**Ticket ID:** {channel.id}"
                        )
                    )
                    log_container.add_item(ui.Separator())

                    author_info_row = ui.ActionRow(
                        ui.Button(
                            label="Ticket Author Info",
                            style=discord.ButtonStyle.secondary,
                            emoji=resolve_component_emoji("<:id_icons:1384041001114407013>"),
                            custom_id=f"author_info_{ticket_data.get('creator_id')}",
                        )
                    )

                    async def author_info_callback(btn_interaction: discord.Interaction):
                        await btn_interaction.response.defer(ephemeral=True)
                        await log_view.send_author_info_container(btn_interaction)

                    author_info_row.children[0].callback = author_info_callback
                    log_container.add_item(author_info_row)

                    log_layout.add_item(log_container)

                    await log_channel.send(view=log_layout)

        closed_layout = TicketClosedLayout(bot, ticket_data)
        await channel.send(view=closed_layout)

        return True, "Ticket closed successfully."

    except Exception as e:
        logger.error(f"Error closing ticket: {e}")
        return False, str(e)
