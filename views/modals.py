import discord
import logging
from utils.helpers import send_ephemeral_container
from utils.theme import PURPLE_HEX, PURPLE_PRIMARY

logger = logging.getLogger("discord")


class TicketModal(discord.ui.Modal):
    def __init__(self, bot, category, guild_id, emoji=None):
        super().__init__(title=f"Create {category} Ticket", timeout=300)
        self.bot = bot
        self.category = category
        self.guild_id = guild_id
        self.emoji = emoji

    reason = discord.ui.TextInput(
        label="Ticket Reason",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your reason for opening this ticket...",
        max_length=1000,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)

            reason = self.reason.value.strip() if self.reason.value else "No reason provided"

            from utils.database import check_user_ticket_limit
            from utils.helpers import check_rate_limit

            user_id = interaction.user.id
            if await check_rate_limit(self.bot, self.guild_id, user_id):
                await send_ephemeral_container(
                    interaction, "You're creating tickets too quickly. Please wait 60 seconds."
                )
                return

            ticket_limit_ok, current_tickets, max_tickets = await check_user_ticket_limit(
                self.bot, self.guild_id, user_id
            )
            if not ticket_limit_ok:
                await send_ephemeral_container(
                    interaction,
                    f"You have reached the maximum ticket limit ({current_tickets}/{max_tickets}). Please close existing tickets first.",
                )
                return

            async with self.bot.db.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM ticket_blacklist WHERE guild_id = ? AND user_id = ?", (self.guild_id, user_id)
                )
                if await cur.fetchone():
                    await send_ephemeral_container(
                        interaction, "You are blacklisted from creating tickets in this server."
                    )
                    return

            async with self.bot.db.cursor() as cur:
                await cur.execute("SELECT maintenance_mode FROM tickets WHERE guild_id = ?", (self.guild_id,))
                result = await cur.fetchone()
                if result and result[0]:
                    await send_ephemeral_container(
                        interaction, "The ticket system is currently under maintenance. Please try again later."
                    )
                    return

            from utils.tickets import create_ticket_channel

            try:
                success, message = await create_ticket_channel(
                    self.bot, interaction.guild, interaction.user, None, self.category, reason
                )

                if success:
                    await send_ephemeral_container(interaction, message)
                else:
                    await send_ephemeral_container(interaction, message)
            except Exception as ticket_error:
                logger.error(f"Error creating ticket: {ticket_error}")
                await send_ephemeral_container(interaction, f"Failed to create ticket: {str(ticket_error)}")

        except Exception as e:
            logger.error(f"Error in ticket modal submission: {e}")
            try:
                await send_ephemeral_container(
                    interaction, "An error occurred while creating your ticket. Please try again."
                )
            except Exception:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        logger.error(f"Modal error: {error}")
        try:
            await send_ephemeral_container(interaction, "An error occurred. Please try again.")
        except Exception:
            pass


class PanelCustomizationModal(discord.ui.Modal):
    def __init__(self, setup_view):
        super().__init__(title="Panel Customization")
        self.setup_view = setup_view

    embed_title = discord.ui.TextInput(
        label="Panel Title", placeholder="e.g., Support Center", default="Support Center", max_length=100, required=True
    )

    embed_description = discord.ui.TextInput(
        label="Panel Description",
        placeholder="e.g., Need assistance? Our expert team is here to help!",
        style=discord.TextStyle.paragraph,
        default="Need assistance? Select a category below to create a support ticket.",
        max_length=500,
        required=True,
    )

    embed_color = discord.ui.TextInput(
        label="Panel Color (Hex Code)",
        placeholder=f"e.g., {PURPLE_HEX} or 0x8B5CF6",
        default=PURPLE_HEX,
        max_length=10,
        required=False,
    )

    embed_footer = discord.ui.TextInput(
        label="Panel Footer",
        placeholder="e.g., Support System",
        default="Support System",
        max_length=100,
        required=False,
    )

    embed_image_url = discord.ui.TextInput(
        label="Panel Image URL (Optional)", placeholder="e.g., https://example.com/image.png", required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)

            self.setup_view.setup_data["embed_title"] = self.embed_title.value
            self.setup_view.setup_data["embed_description"] = self.embed_description.value
            self.setup_view.setup_data["embed_footer"] = self.embed_footer.value
            self.setup_view.setup_data["embed_image_url"] = (
                self.embed_image_url.value if self.embed_image_url.value else None
            )

            color_value = self.embed_color.value.strip()
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

            await send_ephemeral_container(
                interaction,
                f"Panel customization saved.\n\n**Title:** {self.embed_title.value}\n**Color:** #{hex(self.setup_view.setup_data['embed_color'])[2:].upper()}",
            )

        except Exception as e:
            logger.error(f"Error in panel customization modal: {e}")
            await send_ephemeral_container(interaction, f"Error saving customization: {str(e)}")


class TicketSetupModal(discord.ui.Modal):
    def __init__(self, bot):
        super().__init__(title="Ticket System Configuration")
        self.bot = bot

    support_channel = discord.ui.TextInput(
        label="Support Channel ID", placeholder="Enter the channel ID where tickets will be created", required=True
    )

    support_role = discord.ui.TextInput(
        label="Support Role ID", placeholder="Enter the role ID for support staff", required=True
    )

    log_channel = discord.ui.TextInput(
        label="Log Channel ID (Optional)", placeholder="Enter the channel ID for ticket logs", required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)

            channel_id = int(str(self.support_channel.value))
            role_id = int(str(self.support_role.value))
            log_channel_id = int(str(self.log_channel.value)) if self.log_channel.value else None

            channel = interaction.guild.get_channel(channel_id)
            role = interaction.guild.get_role(role_id)
            log_channel = interaction.guild.get_channel(log_channel_id) if log_channel_id else None

            if not channel:
                await send_ephemeral_container(interaction, "Support channel not found.")
                return

            if not role:
                await send_ephemeral_container(interaction, "Support role not found.")
                return

            from utils.database import add_or_update_ticket_config

            saved = await add_or_update_ticket_config(
                self.bot,
                interaction.guild.id,
                channel_id=channel_id,
                role_id=role_id,
                log_channel_id=log_channel_id,
            )
            if not saved:
                raise RuntimeError("Ticket configuration could not be saved")

            await send_ephemeral_container(
                interaction,
                f"Setup complete.\n\n**Support Channel:** {channel.mention}\n**Support Role:** {role.mention}\n**Log Channel:** {log_channel.mention if log_channel else 'None'}",
            )

        except ValueError:
            await send_ephemeral_container(interaction, "Invalid ID format. Please use numbers only.")
        except Exception as e:
            logger.error(f"Error in setup modal: {e}")
            await send_ephemeral_container(interaction, f"Setup failed: {str(e)}")


class TicketLimitModal(discord.ui.Modal):
    def __init__(self, setup_view):
        super().__init__(title="Set Ticket Limit")
        self.setup_view = setup_view

    ticket_limit = discord.ui.TextInput(
        label="Maximum Tickets Per User",
        placeholder="Enter a number between 1-10 (default: 3)",
        default="3",
        min_length=1,
        max_length=2,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            limit = int(self.ticket_limit.value)
            if limit < 1 or limit > 10:
                await send_ephemeral_container(interaction, "Ticket limit must be between 1 and 10.")
                return

            self.setup_view.setup_data["ticket_limit"] = limit
            await send_ephemeral_container(interaction, f"Ticket limit set to {limit}.")

        except ValueError:
            await send_ephemeral_container(interaction, "Please enter a valid number between 1 and 10.")


class SetupModal(discord.ui.Modal):
    def __init__(self, bot):
        super().__init__(title="Ticket System Configuration")
        self.bot = bot
