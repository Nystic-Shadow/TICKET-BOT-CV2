"""Cached, per-server exact-match autoresponders."""

from __future__ import annotations

import logging
from collections import defaultdict

import aiosqlite
import discord
from discord import app_commands, ui
from discord.ext import commands

from utils.config import config
from utils.application_emojis import resolve_emojis
from utils.theme import PURPLE_PRIMARY

logger = logging.getLogger("discord")


def _message_view(title: str, content: str) -> ui.LayoutView:
    view = ui.LayoutView()
    container = ui.Container(accent_color=PURPLE_PRIMARY)
    container.add_item(ui.TextDisplay(resolve_emojis(f"### {title}")))
    container.add_item(ui.Separator())
    container.add_item(ui.TextDisplay(resolve_emojis(content)))
    view.add_item(container)
    return view


class TriggerSystem(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.triggers_db: aiosqlite.Connection | None = None
        self.cache: defaultdict[int, dict[str, str]] = defaultdict(dict)

    async def cog_load(self) -> None:
        await self.setup_triggers_database()

    async def setup_triggers_database(self) -> None:
        if self.triggers_db is None:
            self.triggers_db = await aiosqlite.connect(config.TRIGGERS_DATABASE_PATH)
            self.bot.triggers_db = self.triggers_db

        await self.triggers_db.execute("PRAGMA journal_mode = WAL")
        await self.triggers_db.execute("PRAGMA busy_timeout = 5000")
        await self.triggers_db.execute(
            """
            CREATE TABLE IF NOT EXISTS triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                keyword TEXT NOT NULL,
                message TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(guild_id, keyword)
            )
            """
        )
        await self.triggers_db.commit()
        await self.reload_cache()
        logger.info("Loaded %s cached triggers", sum(map(len, self.cache.values())))

    async def reload_cache(self) -> None:
        if self.triggers_db is None:
            return
        async with self.triggers_db.execute("SELECT guild_id, keyword, message FROM triggers") as cursor:
            rows = await cursor.fetchall()
        self.cache.clear()
        for guild_id, keyword, message in rows:
            self.cache[guild_id][keyword] = message

    async def _send(
        self,
        ctx: commands.Context,
        title: str,
        content: str,
        *,
        ephemeral: bool = True,
    ) -> None:
        await ctx.send(
            view=_message_view(title, content),
            ephemeral=ephemeral,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.hybrid_group(name="trigger", description="Manage keyword triggers")
    @commands.guild_only()
    async def trigger(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @trigger.command(name="add", description="Add an exact-match automatic response.")
    @app_commands.describe(keyword="Exact text to match", message="Response to send")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def trigger_add(self, ctx: commands.Context, keyword: str, *, message: str):
        if ctx.interaction:
            await ctx.defer(ephemeral=True)

        keyword = keyword.casefold().strip()
        if not keyword or "\n" in keyword or len(keyword) > 50:
            await self._send(
                ctx,
                "Invalid Keyword",
                "Use 1-50 characters on a single line.",
            )
            return
        if not message.strip() or len(message) > 2000:
            await self._send(
                ctx,
                "Invalid Response",
                "Use a response between 1 and 2,000 characters.",
            )
            return

        assert self.triggers_db is not None
        try:
            await self.triggers_db.execute(
                """
                INSERT INTO triggers (guild_id, keyword, message, created_by)
                VALUES (?, ?, ?, ?)
                """,
                (ctx.guild.id, keyword, message, ctx.author.id),
            )
            await self.triggers_db.commit()
        except aiosqlite.IntegrityError:
            await self._send(
                ctx,
                "Trigger Already Exists",
                f"A response for `{keyword}` is already configured.",
            )
            return

        self.cache[ctx.guild.id][keyword] = message
        preview = f"{message[:100]}..." if len(message) > 100 else message
        await self._send(
            ctx,
            "Trigger Added",
            f"**Keyword:** `{keyword}`\n\n**Response:**\n{preview}",
        )

    @trigger.command(name="remove", description="Remove a keyword trigger.")
    @app_commands.describe(keyword="Keyword to remove")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def trigger_remove(self, ctx: commands.Context, keyword: str):
        if ctx.interaction:
            await ctx.defer(ephemeral=True)
        keyword = keyword.casefold().strip()

        assert self.triggers_db is not None
        cursor = await self.triggers_db.execute(
            "DELETE FROM triggers WHERE guild_id = ? AND keyword = ?",
            (ctx.guild.id, keyword),
        )
        await self.triggers_db.commit()
        if cursor.rowcount == 0:
            await self._send(ctx, "Not Found", f"No trigger exists for `{keyword}`.")
            return

        self.cache[ctx.guild.id].pop(keyword, None)
        await self._send(ctx, "Trigger Removed", f"Removed the trigger for `{keyword}`.")

    @trigger.command(name="get", description="Show one configured trigger.")
    @app_commands.describe(keyword="Keyword to inspect")
    async def trigger_get(self, ctx: commands.Context, keyword: str):
        if ctx.interaction:
            await ctx.defer(ephemeral=True)
        keyword = keyword.casefold().strip()

        assert self.triggers_db is not None
        async with self.triggers_db.execute(
            """
            SELECT keyword, message, created_by
            FROM triggers
            WHERE guild_id = ? AND keyword = ?
            """,
            (ctx.guild.id, keyword),
        ) as cursor:
            trigger = await cursor.fetchone()
        if not trigger:
            await self._send(ctx, "Not Found", f"No trigger exists for `{keyword}`.")
            return

        keyword, message, created_by = trigger
        creator = self.bot.get_user(created_by)
        if creator is None:
            try:
                creator = await self.bot.fetch_user(created_by)
            except discord.HTTPException:
                creator = None
        creator_name = f"{creator.display_name} ({creator.id})" if creator else f"Unknown user ({created_by})"
        await self._send(
            ctx,
            "Trigger Details",
            f"**Keyword:** `{keyword}`\n\n**Created by:** {creator_name}\n\n**Response:**\n{message}",
        )

    @trigger.command(name="list", description="List active triggers in this server.")
    async def trigger_list(self, ctx: commands.Context):
        if ctx.interaction:
            await ctx.defer(ephemeral=True)

        triggers = sorted(self.cache.get(ctx.guild.id, {}).items())
        if not triggers:
            await self._send(ctx, "Triggers", "No triggers are configured for this server.")
            return

        lines = []
        for keyword, message in triggers[:15]:
            preview = f"{message[:40]}..." if len(message) > 40 else message
            lines.append(f"`{keyword}` - {preview}")
        if len(triggers) > 15:
            lines.append(f"\n...and {len(triggers) - 15} more")
        await self._send(
            ctx,
            "Active Triggers",
            f"**{len(triggers)} trigger(s)**\n\n" + "\n".join(lines),
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        response = self.cache.get(message.guild.id, {}).get(message.content.casefold().strip())
        if response is None:
            return
        try:
            await message.channel.send(
                view=_message_view("Automatic Response", response),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.exception("Failed to send trigger response in channel %s", message.channel.id)

    async def cog_unload(self) -> None:
        if self.triggers_db is not None:
            await self.triggers_db.close()
            self.triggers_db = None
            self.bot.triggers_db = None


async def setup(bot):
    await bot.add_cog(TriggerSystem(bot))
