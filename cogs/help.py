import discord
from discord.ext import commands
from discord import app_commands, ui
import logging
from utils.helpers import utc_to_gmt, send_ephemeral_container
from utils.config import config
from utils.application_emojis import resolve_emojis
from utils.theme import PURPLE_PRIMARY
import time

logger = logging.getLogger("discord")


class HelpLayout(ui.LayoutView):
    def __init__(self, bot, user_id):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id

        self.container = ui.Container(accent_color=PURPLE_PRIMARY)
        self.setup_main_content()
        self.add_item(self.container)

    def setup_main_content(self):
        self.container.clear_items()

        self.container.add_item(ui.TextDisplay(resolve_emojis("# <:icons_help:1382704281945112645> Bot Help Center")))

        self.container.add_item(ui.Separator())

        welcome_text = (
            f"Welcome to **{self.bot.user.name}** - your ticket support system! "
            f"Select a category below to get detailed information about features and commands."
        )

        welcome_section = ui.Section(accessory=ui.Thumbnail(media=self.bot.user.display_avatar.url))
        welcome_section.add_item(ui.TextDisplay(welcome_text))
        self.container.add_item(welcome_section)

        self.container.add_item(ui.Separator())

        self.category_select = ui.ActionRow(
            ui.Select(
                placeholder="Select a help category...",
                options=[
                    discord.SelectOption(label="Home", value="home", description="Return to main help page"),
                    discord.SelectOption(label="Setup Guide", value="setup", description="Complete setup walkthrough"),
                    discord.SelectOption(
                        label="Ticket Commands", value="tickets", description="All ticket management commands"
                    ),
                    discord.SelectOption(
                        label="Admin Commands", value="admin", description="Administrator commands & features"
                    ),
                    discord.SelectOption(
                        label="Trigger Commands", value="triggers", description="Keyword triggers & auto-responses"
                    ),
                    discord.SelectOption(
                        label="General Commands", value="general", description="General bot commands & info"
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
        elif choice == "setup":
            await self.show_setup_content(interaction)
        elif choice == "tickets":
            await self.show_tickets_content(interaction)
        elif choice == "admin":
            await self.show_admin_content(interaction)
        elif choice == "triggers":
            await self.show_triggers_content(interaction)
        elif choice == "general":
            await self.show_general_content(interaction)

    async def show_setup_content(self, interaction: discord.Interaction):
        try:
            self.container.clear_items()

            self.container.add_item(
                ui.TextDisplay(resolve_emojis("### <:icons_wrench:1382702984940617738> Setup Guide"))
            )
            self.container.add_item(ui.Separator())

            content = (
                "**<:UA_Rocket_icons:1382701592851124254> Quick Setup**\n"
                "`setup` - Launch the complete setup wizard\n"
                "This command will guide you through configuring categories, roles, and channels.\n\n"
                "**<:clipboard1:1383857546410070117> Category Management**\n"
                "`category add <name>` - Add a new support category\n"
                "`category remove <name>` - Remove an existing category\n"
                "`category list` - View all configured categories\n\n"
                "**<:Target:1382706193855942737> Panel Deployment**\n"
                "`sendpanel dropdown` - Send panel with dropdown menu\n"
                "`sendpanel button` - Send panel with individual buttons"
            )
            self.container.add_item(ui.TextDisplay(resolve_emojis(content)))

            self.container.add_item(ui.Separator())
            self.container.add_item(self.category_select)

            await interaction.response.edit_message(view=self)
        except Exception as e:
            logger.error(f"Error in show_setup_content: {e}")

    async def show_tickets_content(self, interaction: discord.Interaction):
        try:
            self.container.clear_items()

            self.container.add_item(
                ui.TextDisplay(resolve_emojis("### <:Ticket_icons:1382703084815257610> Ticket Commands"))
            )
            self.container.add_item(ui.Separator())

            content = (
                "**<:Target:1382706193855942737> Ticket Management**\n"
                "`close` - Close the current ticket\n"
                "`reopen` - Reopen a closed ticket\n"
                "`claim` - Claim a ticket for support\n"
                "`ticket transfer @user` - Transfer ticket to another staff member\n"
                "`ticket adduser @user` - Add a user to the current ticket\n"
                "`ticket removeuser @user` - Remove a user from the ticket\n"
                "`rename <name>` - Rename the current ticket channel\n\n"
                "**<:icons_clock:1382701751206936697> Status**\n"
                "`ticket info` - View ticket information\n\n"
                "**<:lightbulb:1382701619753386035> User Features**\n"
                "Create tickets using the support panel\n"
                "Receive a transcript in DMs when staff delete the closed ticket"
            )
            self.container.add_item(ui.TextDisplay(resolve_emojis(content)))

            self.container.add_item(ui.Separator())
            self.container.add_item(self.category_select)

            await interaction.response.edit_message(view=self)
        except Exception as e:
            logger.error(f"Error in show_tickets_content: {e}")

    async def show_admin_content(self, interaction: discord.Interaction):
        try:
            self.container.clear_items()

            self.container.add_item(ui.TextDisplay(resolve_emojis("### <:shield:1382703287891136564> Admin Commands")))
            self.container.add_item(ui.Separator())

            content = (
                "**<:icons_wrench:1382702984940617738> System Setup**\n"
                "`setup` - Configure the entire support system\n"
                "`sendpanel <type>` - Deploy support panels\n"
                "`category reset` - Reset all categories to default\n\n"
                "**<:people_icons:1384040549937451068> Support Role Management**\n"
                "`supportrole add @role` - Add additional support role\n"
                "`supportrole remove @role` - Remove additional support role\n"
                "`supportrole list` - List all support roles\n\n"
                "**<:shield:1382703287891136564> Permissions Required**\n"
                "Most admin commands require **Administrator** permission or designated **Support Staff** role."
            )
            self.container.add_item(ui.TextDisplay(resolve_emojis(content)))

            self.container.add_item(ui.Separator())
            self.container.add_item(self.category_select)

            await interaction.response.edit_message(view=self)
        except Exception as e:
            logger.error(f"Error in show_admin_content: {e}")

    async def show_triggers_content(self, interaction: discord.Interaction):
        try:
            self.container.clear_items()

            self.container.add_item(
                ui.TextDisplay(resolve_emojis("### <:features_icons:1383850989722796053> Trigger Commands"))
            )
            self.container.add_item(ui.Separator())

            content = (
                "**<:icons_wrench:1382702984940617738> Trigger Management**\n"
                "`trigger add <keyword> <message>` - Create a new keyword trigger\n"
                "`trigger remove <keyword>` - Remove an existing trigger\n"
                "`trigger get <keyword>` - View trigger response message\n\n"
                "**<:clipboard1:1383857546410070117> Trigger Information**\n"
                "`trigger list` - View all triggers in this server\n"
                "Triggers respond automatically when exact keyword is sent\n\n"
                "**<:shield:1382703287891136564> Permissions Required**\n"
                "`trigger add` and `trigger remove` require **Administrator** permission\n"
                "`trigger get` and `trigger list` can be used by anyone"
            )
            self.container.add_item(ui.TextDisplay(resolve_emojis(content)))

            self.container.add_item(ui.Separator())
            self.container.add_item(self.category_select)

            await interaction.response.edit_message(view=self)
        except Exception as e:
            logger.error(f"Error in show_triggers_content: {e}")

    async def show_general_content(self, interaction: discord.Interaction):
        try:
            self.container.clear_items()

            self.container.add_item(
                ui.TextDisplay(resolve_emojis("### <:icons_help:1382704281945112645> General Commands"))
            )
            self.container.add_item(ui.Separator())

            support_resource = ""
            if config.SUPPORT_SERVER:
                support_resource = (
                    f"Join the **{self.bot.user.name}** support server: "
                    f"[Open invite]({config.SUPPORT_SERVER})\n"
                )

            content = (
                "**<:stats_1:1382703019334045830> Bot Information**\n"
                "`ping` - Check bot latency and status\n"
                "`botinfo` - View detailed bot information\n"
                "`help` - Display this help menu\n\n"
                "**<:icons_help:1382704281945112645> Support Resources**\n"
                "`faq` - Frequently asked questions\n\n"
                "**<:UA_Rocket_icons:1382701592851124254> Need More Help?**\n"
                f"{support_resource}"
                "Create a support ticket using the panel for personalized assistance!"
            )
            self.container.add_item(ui.TextDisplay(resolve_emojis(content)))

            self.container.add_item(ui.Separator())
            self.container.add_item(self.category_select)

            await interaction.response.edit_message(view=self)
        except Exception as e:
            logger.error(f"Error in show_general_content: {e}")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_timeout(self):
        pass


class HelpSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.start_time = time.time()

    async def cog_before_invoke(self, ctx: commands.Context) -> None:
        if ctx.interaction and not ctx.interaction.response.is_done():
            await ctx.defer(ephemeral=True)

    @commands.hybrid_command(name="help", description="Display help information and available commands.")
    async def help_command(self, ctx: commands.Context):
        invoker = ctx.author
        logger.info(f"Help command invoked by {invoker}")
        try:
            help_view = HelpLayout(self.bot, invoker.id)
            await ctx.send(view=help_view, ephemeral=ctx.interaction is not None)

        except Exception as e:
            logger.error(f"Error in help command: {e}")
            raise e

    @commands.hybrid_command(name="botinfo", aliases=["bi"], description="Display information about the bot.")
    @app_commands.describe()
    async def botinfo(self, ctx: commands.Context):
        logger.info(f"Botinfo command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}")
        try:
            view = ui.LayoutView()
            container = ui.Container(accent_color=PURPLE_PRIMARY)

            container.add_item(ui.TextDisplay(f"### {self.bot.user.name} Statistics"))
            container.add_item(ui.Separator())

            info_text = (
                f"<:stats_1:1382703019334045830> **General Information**\n"
                f"- **Bot Developer:** {config.DEVELOPER_NAME}\n"
                f"- **Bot ID:** `{self.bot.user.id}`\n"
                f"- **Created At:** `{utc_to_gmt(self.bot.user.created_at).strftime('%Y-%m-%d %H:%M:%S GMT')}`\n\n"
                f"<:clipboard1:1383857546410070117> **Technical Details**\n"
                f"- **Discord.py Version:** `{discord.__version__}`\n"
                f"- **Python Version:** `{__import__('platform').python_version()}`\n"
                f"- **Total Servers:** `{len(self.bot.guilds)}`"
            )

            info_section = ui.Section(accessory=ui.Thumbnail(media=self.bot.user.display_avatar.url))
            info_section.add_item(ui.TextDisplay(resolve_emojis(info_text)))
            container.add_item(info_section)

            view.add_item(container)

            await ctx.send(view=view, ephemeral=ctx.interaction is not None)

        except Exception as e:
            logger.error(f"Error in botinfo command: {e}")
            raise e

    @commands.hybrid_command(name="ping", description="Check the bot's latency and connection status.")
    async def ping(self, ctx: commands.Context):
        logger.info(f"Ping command invoked by {ctx.author if isinstance(ctx, commands.Context) else ctx.user}")
        try:
            latency = round(self.bot.latency * 1000)
            websocket_latency = round(self.bot.latency * 1000)

            view = ui.LayoutView()
            container = ui.Container(accent_color=PURPLE_PRIMARY)

            container.add_item(ui.TextDisplay("### Bot's Latency"))
            container.add_item(ui.Separator())

            content = f"- **Ping Latency:** `{latency}ms`\n- **WebSocket Latency:** `{websocket_latency}ms`"
            container.add_item(ui.TextDisplay(content))

            view.add_item(container)

            await ctx.send(view=view, ephemeral=ctx.interaction is not None)

        except Exception as e:
            logger.error(f"Error in ping command: {e}")
            raise e


async def setup(bot):
    await bot.add_cog(HelpSystem(bot))
