import discord
from discord.ext import commands
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path
from utils.config import config

# Force UTF-8 encoding for Windows consoles
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

logger = logging.getLogger("discord")


def print_bot_ready(bot_name):
    ascii_art = """
 ▄████▄   ▒░       ░  ░ ░    ░
░                  ░
"""
    print(ascii_art)
    print(f"\033[92mLogin successful logged in as {bot_name}\033[0m")


def print_error(message):
    print(f"\033[91m[ERROR] {message}\033[0m")


def print_loading(message):
    colors = ["\033[91m", "\033[93m", "\033[92m", "\033[96m", "\033[94m", "\033[95m"]
    color = colors[hash(message) % len(colors)]
    print(f"{color}◆ {message}...\033[0m")


def print_success(message):
    colors = ["\033[92m", "\033[96m", "\033[94m", "\033[95m"]
    color = colors[hash(message) % len(colors)]
    print(f"{color}✓ {message}\033[0m")


def print_rainbow_separator():
    rainbow = "\033[91m▆\033[93m▆\033[92m▆\033[96m▆\033[94m▆\033[95m▆\033[0m"
    print(f"  {rainbow * 12}")


def print_system_ready():
    print_rainbow_separator()
    print("\033[92m  System Operational\033[0m")
    print(f"\033[95m Developed by {config.DEVELOPER_NAME}\033[0m")
    print_rainbow_separator()


class TicketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True

        super().__init__(
            command_prefix=config.PREFIX,
            intents=intents,
            help_command=None,
            heartbeat_timeout=60.0,
            chunk_guilds_at_startup=False,
        )

        self.db = None
        self.triggers_db = None
        self.active_setups = {}
        self.start_time = datetime.now()
        self._ready_initialized = False
        self.application_emoji_report = None

    async def setup_database(self):
        from utils.database import initialize_database

        print_loading("Database initialization")
        await initialize_database(self)
        print_success("Database initialized")

    async def setup_application_emojis(self):
        """Mirror legacy emoji assets to the application before views load."""

        from utils.application_emojis import (
            PROJECT_ROOT,
            ApplicationEmojiSynchronizer,
            load_emoji_manifest,
        )

        emoji_directory = Path(config.EMOJI_DIRECTORY)
        if not emoji_directory.is_absolute():
            emoji_directory = PROJECT_ROOT / emoji_directory

        if not config.AUTO_SYNC_APPLICATION_EMOJIS or not config.TOKEN:
            load_emoji_manifest(emoji_directory / "manifest.json")
            logger.info("Automatic application emoji sync is disabled or no bot token is available")
            return

        application_id = self.application_id or getattr(self.user, "id", None)
        if not application_id:
            load_emoji_manifest(emoji_directory / "manifest.json")
            logger.warning("Application emoji sync skipped because the application ID is unavailable")
            return

        print_loading("Synchronizing application emojis")
        synchronizer = ApplicationEmojiSynchronizer(
            token=config.TOKEN,
            application_id=application_id,
            source_root=PROJECT_ROOT,
            emoji_directory=emoji_directory,
        )
        try:
            self.application_emoji_report = await asyncio.wait_for(synchronizer.sync(upload=True), timeout=180.0)
            report = self.application_emoji_report
            print_success(
                f"Application emojis ready - {report.uploaded} uploaded, "
                f"{report.replaced} purple replacements, {report.reused} reused, "
                f"{report.downloaded} downloaded"
            )
            if report.failures:
                logger.warning(
                    "Application emoji sync completed with %d failure(s): %s", len(report.failures), report.failures
                )
        except Exception:
            load_emoji_manifest(emoji_directory / "manifest.json")
            logger.exception("Application emoji sync failed; continuing with the last local mapping")

    async def setup_hook(self):
        try:
            await self.setup_database()
            await self.setup_application_emojis()

            print_loading("Loading modules")
            extensions = ["cogs.tickets", "cogs.help", "cogs.triggers", "cogs.on_mention", "utils.error_handler"]

            for extension in extensions:
                try:
                    await self.load_extension(extension)
                    print_success(f"✓ {extension} loaded")
                except Exception as e:
                    print_error(f"✗ Failed to load {extension}: {e}")
                    raise

            hybrid_commands = [cmd for cmd in self.commands if hasattr(cmd, "app_command")]
            print_success(f"Modules loaded - {len(hybrid_commands)} hybrid commands registered")

        except Exception as e:
            print_error(f"Setup failed: {e}")
            raise

    async def on_ready(self):
        if self._ready_initialized:
            logger.info("Discord connection resumed as %s", self.user)
            return

        try:
            print_loading("Initializing persistent views")
            from views.panel_views import PersistentTicketPanelView, PersistentTicketButton
            from views.ticket_views import (
                TicketControlView,
                PersistentTicketClosedView,
                PersistentPanelSelectView,
                PersistentPanelButton,
            )
            from utils.database import get_ticket_categories

            self.add_view(TicketControlView(self, {}))

            self.add_view(PersistentTicketPanelView())

            self.add_view(PersistentTicketClosedView())

            self.add_view(PersistentPanelSelectView())

            self.add_dynamic_items(PersistentPanelButton)

            registered_categories = set()
            for guild in self.guilds:
                try:
                    categories = await get_ticket_categories(self, guild.id)
                    if categories:
                        for category_name, emoji in categories:
                            if category_name not in registered_categories:
                                button_view = discord.ui.View(timeout=None)
                                button_view.add_item(PersistentTicketButton(category_name, emoji, 0))
                                self.add_view(button_view)
                                registered_categories.add(category_name)
                except Exception as e:
                    logger.warning(f"Guild {guild.id} category load failed: {e}")

            print_success(f"Views registered ({len(registered_categories)} button categories)")

            print_loading("Synchronizing slash commands")
            try:
                synced = await asyncio.wait_for(self.tree.sync(), timeout=30.0)
                print_success(f"Commands synchronized ({len(synced)} commands)")

                for cmd in synced:
                    logger.info(f"Synced command: /{cmd.name}")

            except asyncio.TimeoutError:
                print_error("Command sync timed out - bot will continue running")
            except discord.HTTPException as e:
                if "429" in str(e):
                    print_error("Rate limited during command sync - commands will sync later")
                else:
                    print_error(f"Command sync failed: {e}")
            except Exception as e:
                print_error(f"Command sync failed: {e}")

            try:
                status_type = config.BOT_STATUS_TYPE

                if status_type == "STREAMING":
                    if config.STREAM_URL:
                        activity = discord.Streaming(name=config.BOT_STATUS, url=config.STREAM_URL)
                    else:
                        activity = discord.Game(name=config.BOT_STATUS)
                        logger.warning("STREAM_URL is empty; using a playing activity instead")
                    status = discord.Status.online
                elif status_type == "PLAYING":
                    activity = discord.Game(name=config.BOT_STATUS)
                    status = discord.Status.online
                elif status_type == "WATCHING":
                    activity = discord.Activity(type=discord.ActivityType.watching, name=config.BOT_STATUS)
                    status = discord.Status.online
                elif status_type == "LISTENING":
                    activity = discord.Activity(type=discord.ActivityType.listening, name=config.BOT_STATUS)
                    status = discord.Status.online
                else:
                    activity = discord.Game(name=config.BOT_STATUS)
                    status = discord.Status.online

                if config.BOT_STATUS_TYPE == "IDLE":
                    status = discord.Status.idle
                elif config.BOT_STATUS_TYPE == "DND":
                    status = discord.Status.dnd
                elif config.BOT_STATUS_TYPE == "INVISIBLE":
                    status = discord.Status.invisible

                await self.change_presence(activity=activity, status=status)
                print_success(f"Bot status set: {config.BOT_STATUS} ({status_type})")
            except Exception as status_error:
                print_error(f"Failed to set bot status: {status_error}")

            print_bot_ready(self.user.name)
            print_system_ready()
            self._ready_initialized = True

        except Exception as e:
            print_error(f"Startup error: {e}")
            if hasattr(self, "user") and self.user:
                print_bot_ready(self.user.name)

    async def close(self):
        print_loading("Shutting down bot")

        if hasattr(self, "db") and self.db:
            try:
                await self.db.close()
                print_success("Main database connection closed")
            except Exception as e:
                print_error(f"Error closing main database: {e}")

        if hasattr(self, "triggers_db") and self.triggers_db:
            try:
                await self.triggers_db.close()
                print_success("Triggers database connection closed")
            except Exception as e:
                print_error(f"Error closing triggers database: {e}")

        try:
            await super().close()
            print_success("Bot shutdown complete")
        except Exception as e:
            print_error(f"Error during bot shutdown: {e}")


bot = TicketBot()


@bot.command()
@commands.is_owner()
async def sync(ctx):
    """Sync commands globally"""
    print_loading("Manual sync initiated...")
    try:
        synced = await bot.tree.sync()
        print_success(f"Synced {len(synced)} commands globally")
        await ctx.send(f"Synced {len(synced)} commands globally")
    except Exception as e:
        print_error(f"Sync failed: {e}")
        await ctx.send(f"Sync failed: {e}")


async def shutdown_handler():
    """Handle graceful shutdown"""
    print_loading("Shutdown signal received")
    await bot.close()


if __name__ == "__main__":
    config.setup_logging()
    if not config.TOKEN:
        logger.error("DISCORD_TOKEN is required and was not found in the environment")
        raise SystemExit(1)

    print("Bot logging in...")

    try:
        asyncio.run(bot.start(config.TOKEN))
    except discord.LoginFailure:
        print_error("Invalid bot token. Rotate it and check DISCORD_TOKEN.")
    except KeyboardInterrupt:
        print_loading("Shutdown initiated by user")
        asyncio.run(shutdown_handler())
    except Exception as e:
        print_error(f"Bot failed to start: {e}")
    finally:
        print_success("Bot process ended")
