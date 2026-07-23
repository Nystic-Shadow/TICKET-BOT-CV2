import logging
import secrets
import traceback
import discord
from discord.ext import commands
from utils.helpers import send_ephemeral_container
from utils.application_emojis import resolve_emojis
from utils.theme import PURPLE_PRIMARY

logger = logging.getLogger("discord")


class GlobalErrorHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def create_error_container(self, title: str, description: str, error_type: str = "general"):
        """Create a standardized error container using Components V2"""
        from discord import ui

        layout_view = ui.LayoutView()
        container = ui.Container(accent_color=PURPLE_PRIMARY)
        container.add_item(ui.TextDisplay(resolve_emojis(f"### {title}")))
        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay(resolve_emojis(description)))
        layout_view.add_item(container)

        return layout_view

    async def send_error_response(self, ctx, view):
        """Send error response handling both interactions and regular commands using Components v2"""
        try:
            if isinstance(ctx, discord.Interaction):
                if not ctx.response.is_done():
                    await ctx.response.send_message(view=view, ephemeral=True)
                else:
                    await ctx.followup.send(view=view, ephemeral=True)
            else:
                await ctx.send(
                    view=view,
                    ephemeral=getattr(ctx, "interaction", None) is not None,
                )
        except Exception as e:
            try:
                await send_ephemeral_container(ctx, "An error occurred")
            except Exception:
                logger.error(f"Failed to send error message: {e}")

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        """Handle all command errors globally"""

        ignored = (commands.CommandNotFound, commands.DisabledCommand)
        if isinstance(error, ignored):
            return

        logger.error(f"Command error in {ctx.command}: {error}")
        logger.error(
            "Command exception",
            exc_info=(type(error), error, error.__traceback__),
        )

        if isinstance(error, commands.MissingPermissions):
            embed = self.create_error_container(
                "Missing Permissions",
                f"**You don't have the required permissions to use this command.**\n\n"
                f"**Required Permissions:**\n{', '.join(error.missing_permissions)}\n\n"
                f"<:lightbulb:1382701619753386035> Contact an administrator if you believe this is an error.",
                "permission",
            )

        elif isinstance(error, commands.BotMissingPermissions):
            embed = self.create_error_container(
                "Bot Missing Permissions",
                f"**I don't have the required permissions to execute this command.**\n\n"
                f"**Missing Permissions:**\n{', '.join(error.missing_permissions)}\n\n"
                f"<:icons_wrench:1382702984940617738> Please ask an administrator to grant me these permissions.",
                "bot_permission",
            )

        elif isinstance(error, commands.CommandOnCooldown):
            embed = self.create_error_container(
                "Command Cooldown",
                f"**This command is on cooldown.**\n\n"
                f"<:icons_clock:1382701751206936697> **Try again in:** {error.retry_after:.1f} seconds\n\n"
                f"Cooldowns help prevent spam and ensure optimal performance.",
                "cooldown",
            )

        elif isinstance(error, commands.MissingRequiredArgument):
            if ctx.command and ctx.command.qualified_name == "sendpanel":
                embed = self.create_error_container(
                    "Missing Required Argument",
                    f"Invalid panel type. Use `{ctx.prefix}sendpanel dropdown` or `{ctx.prefix}sendpanel button`.",
                    "validation",
                )
            else:
                embed = self.create_error_container(
                    "Missing Required Argument",
                    f"**Missing required argument: `{error.param.name}`**\n\n"
                    f"<:clipboard1:1383857546410070117> **Usage:** `{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`\n\n"
                    f"<:lightbulb:1382701619753386035> Use `{ctx.prefix}help {ctx.command.qualified_name}` for more information.",
                    "validation",
                )

        elif isinstance(error, commands.BadArgument):
            embed = self.create_error_container(
                "Invalid Argument",
                f"**Invalid argument provided.**\n\n"
                f"<:clipboard1:1383857546410070117> **Usage:** `{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`\n\n"
                f"**Error Details:** {str(error)}\n\n"
                f"<:lightbulb:1382701619753386035> Use `{ctx.prefix}help {ctx.command.qualified_name}` for more information.",
                "validation",
            )

        elif isinstance(error, commands.NotOwner):
            embed = self.create_error_container(
                "Owner Only Command",
                "**This command is restricted to the bot owner only.**\n\n"
                "<:shield:1382703287891136564> This is a developer command and cannot be used by regular users.",
                "permission",
            )

        elif isinstance(error, commands.NSFWChannelRequired):
            embed = self.create_error_container(
                "NSFW Channel Required",
                "**This command can only be used in NSFW channels.**\n\n"
                "<:warning:1382701413284446228> Please use this command in an appropriate channel.",
                "validation",
            )

        elif isinstance(error, discord.Forbidden):
            embed = self.create_error_container(
                "Permission Denied",
                "**I don't have permission to perform this action.**\n\n"
                "<:icons_wrench:1382702984940617738> Please check my permissions and try again.",
                "bot_permission",
            )

        elif isinstance(error, discord.NotFound):
            embed = self.create_error_container(
                "Resource Not Found",
                "**The requested resource could not be found.**\n\n"
                "<:Target:1382706193855942737> This might be a deleted channel, message, or user.",
                "not_found",
            )

        elif isinstance(error, discord.HTTPException):
            if "rate limit" in str(error).lower():
                embed = self.create_error_container(
                    "Rate Limited",
                    "**Discord is rate limiting the bot.**\n\n"
                    "<:icons_clock:1382701751206936697> Please wait a moment and try again.\n\n"
                    "This helps prevent spam and keeps Discord stable.",
                    "network",
                )
            else:
                embed = self.create_error_container(
                    "Discord API Error",
                    "**An error occurred while communicating with Discord.**\n\n"
                    "<:icons_refresh:1382701477759549523> Please try again in a moment.",
                    "network",
                )

        elif "database" in str(error).lower() or "sqlite" in str(error).lower():
            embed = self.create_error_container(
                "Database Error",
                "**A database error occurred.**\n\n"
                "<:disk_icons:1384042698192715899> Our team has been notified and will fix this soon.\n\n"
                "<:icons_refresh:1382701477759549523> Please try again in a few minutes.",
                "database",
            )

        else:
            error_id = secrets.token_hex(4)
            embed = self.create_error_container(
                "Unexpected Error",
                f"**An unexpected error occurred while executing this command.**\n\n"
                f"Please try again. If the issue persists, contact support.\n\n"
                f"**Error ID:** `{error_id}`",
                "general",
            )

            logger.error(f"Error ID {error_id}: {str(error)}")

        await self.send_error_response(ctx, embed)

    @commands.Cog.listener()
    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        """Handle all application command errors globally"""

        logger.error(f"App command error: {error}")
        logger.error(
            "Application command exception",
            exc_info=(type(error), error, error.__traceback__),
        )

        if isinstance(error, discord.app_commands.MissingPermissions):
            embed = self.create_error_container(
                "Missing Permissions",
                f"**You don't have the required permissions to use this command.**\n\n"
                f"**Required Permissions:**\n{', '.join(error.missing_permissions)}\n\n"
                f"<:lightbulb:1382701619753386035> Contact an administrator if you believe this is an error.",
                "permission",
            )

        elif isinstance(error, discord.app_commands.BotMissingPermissions):
            embed = self.create_error_container(
                "Bot Missing Permissions",
                f"**I don't have the required permissions to execute this command.**\n\n"
                f"**Missing Permissions:**\n{', '.join(error.missing_permissions)}\n\n"
                f"<:icons_wrench:1382702984940617738> Please ask an administrator to grant me these permissions.",
                "bot_permission",
            )

        elif isinstance(error, discord.app_commands.MissingRole):
            embed = self.create_error_container(
                "Missing Role",
                f"**You need a specific role to use this command.**\n\n"
                f"**Required Role:** {error.missing_role}\n\n"
                f"<:icons_Person:1382703571056853082> Contact an administrator to get the required role.",
                "permission",
            )

        elif isinstance(error, discord.app_commands.CommandOnCooldown):
            embed = self.create_error_container(
                "Command Cooldown",
                f"**This command is on cooldown.**\n\n"
                f"<:icons_clock:1382701751206936697> **Try again in:** {error.retry_after:.1f} seconds\n\n"
                f"Cooldowns help prevent spam and ensure optimal performance.",
                "cooldown",
            )

        elif isinstance(error, discord.app_commands.TransformerError):
            embed = self.create_error_container(
                "Invalid Input",
                f"**Invalid input provided.**\n\n"
                f"<:warning:1382701413284446228> Please check your input and try again.\n\n"
                f"**Error:** {str(error)}",
                "validation",
            )

        else:
            error_id = secrets.token_hex(4)
            embed = self.create_error_container(
                "Command Error",
                f"**An error occurred while executing this command.**\n\n"
                f"Please try again. If the issue persists, contact support.\n\n"
                f"**Error ID:** `{error_id}`",
                "general",
            )

        await self.send_error_response(interaction, embed)

    @commands.Cog.listener()
    async def on_error(self, event, *args, **kwargs):
        """Handle all other bot errors"""
        logger.error(f"Bot error in event {event}")
        logger.error(traceback.format_exc())

        print(f"\033[91m[GLOBAL ERROR] Event: {event}\033[0m")

    async def handle_view_error(self, interaction: discord.Interaction, error: Exception, error_type: str = "general"):
        """Handle errors from Discord UI views"""
        logger.error(f"View error: {error}")
        logger.error(traceback.format_exc())

        embed = self.create_error_container(
            "Interface Error",
            "**An error occurred with the user interface.**\n\n"
            "<:icons_refresh:1382701477759549523> Please try again or refresh the interface.",
            error_type,
        )

        await self.send_error_response(interaction, embed)

    async def handle_database_error(self, ctx, error: Exception):
        """Handle database-specific errors"""
        logger.error(f"Database error: {error}")
        logger.error(traceback.format_exc())

        embed = self.create_error_container(
            "Database Connection Error",
            "**Unable to connect to the database.**\n\n"
            "<:disk_icons:1384042698192715899> This is usually temporary and resolves quickly.\n\n"
            "<:icons_refresh:1382701477759549523> Please try again in a moment.",
            "database",
        )

        await self.send_error_response(ctx, embed)


async def setup(bot):
    await bot.add_cog(GlobalErrorHandler(bot))
