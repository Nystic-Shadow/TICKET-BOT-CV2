import logging
import discord
import time
import re
import io
from datetime import datetime, timezone
from typing import Tuple, Dict, Any, Optional
from utils.variables import replace_variables, build_ticket_context
from utils.application_emojis import resolve_emojis
from utils.theme import PURPLE_PRIMARY


logger = logging.getLogger("discord")


def utc_to_gmt(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def check_rate_limit(bot, guild_id: int, user_id: int, cooldown_seconds: int = 60) -> bool:
    """
    Check if user is rate limited.
    Returns True if user IS rate limited (should be blocked)
    Returns False if user is NOT rate limited (can proceed)
    """
    try:
        current_time = time.time()
        async with bot.db.cursor() as cur:
            await cur.execute(
                "SELECT last_ticket_time FROM rate_limits WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            result = await cur.fetchone()

            if result:
                last_time = result[0]
                if current_time - last_time < cooldown_seconds:
                    return True  # User IS rate limited

            return False  # User is NOT rate limited
    except Exception as e:
        logger.error(f"Error checking rate limit: {e}")
        return False  # Allow on error


async def set_rate_limit(bot, guild_id: int, user_id: int):
    try:
        current_time = time.time()
        async with bot.db.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO rate_limits (guild_id, user_id, last_ticket_time)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id)
                DO UPDATE SET last_ticket_time = excluded.last_ticket_time
                """,
                (guild_id, user_id, current_time),
            )
            await bot.db.commit()
    except Exception as e:
        logger.error(f"Error setting rate limit: {e}")


async def validate_ticket_setup(bot, guild_id: int) -> Tuple[bool, str]:
    try:
        async with bot.db.cursor() as cur:
            await cur.execute("SELECT channel_id, role_id FROM tickets WHERE guild_id = ?", (guild_id,))
            result = await cur.fetchone()

            if not result:
                return False, "Ticket system not configured"

            channel_id, role_id = result
            guild = bot.get_guild(guild_id)

            if not guild:
                return False, "Guild not found."

            if not guild.get_channel(channel_id):
                return False, "Support channel not found or deleted."

            if not guild.get_role(role_id):
                return False, "Support role not found or deleted."

            return True, "Setup valid"
    except Exception as e:
        logger.error(f"Error validating setup: {e}")
        return False, f"Database error: {e}"


async def generate_transcript(channel) -> Tuple[str, io.BytesIO]:
    try:
        transcript_content = f"Transcript for #{channel.name}\n"
        transcript_content += f"Generated on: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        transcript_content += "=" * 50 + "\n\n"

        messages = []
        async for message in channel.history(limit=None, oldest_first=True):
            timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
            author = f"{message.author.display_name} ({message.author.id})"
            content = message.content or "[No content]"

            if message.attachments:
                attachment_urls = [att.url for att in message.attachments]
                content += f"\nAttachments: {', '.join(attachment_urls)}"

            if message.embeds:
                content += f"\n[{len(message.embeds)} embed(s)]"

            messages.append(f"[{timestamp}] {author}: {content}\n")

        transcript_content += "\n".join(messages)
        transcript_file = io.BytesIO(transcript_content.encode("utf-8"))
        return transcript_content, transcript_file

    except Exception as e:
        logger.error(f"Error generating transcript: {e}")
        error_content = f"Error generating transcript: {str(e)}"
        error_file = io.BytesIO(error_content.encode("utf-8"))
        return error_content, error_file


def get_status_emoji(status: str) -> str:
    status_emojis = {
        "open": "<a:green_circle2:1382704526057930794>",
        "closed": "<:icons_Wrong:1382701332955402341>",
        "locked": "<:icons_locked:1382701901685985361>",
        "claimed": "<:welcome:1382706419765350480>",
    }
    return resolve_emojis(status_emojis.get(status, "<:icons_help:1382704281945112645>"))


def sanitize_channel_name(name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9\-_]", "-", name.lower())
    name = re.sub(r"-+", "-", name)
    name = name.strip("-")
    return name[:100] if len(name) > 100 else name


async def send_transcript_dm(user, channel_name, transcript_file):
    try:
        from discord import ui

        transcript_file.seek(0)
        file = discord.File(transcript_file, filename=f"{channel_name}-transcript.txt")

        layout_view = ui.LayoutView()
        container = ui.Container(accent_color=PURPLE_PRIMARY)
        container.add_item(ui.TextDisplay("### Ticket Transcript"))
        container.add_item(ui.Separator())
        container.add_item(
            ui.TextDisplay(
                f"Complete conversation log for your support ticket.\n\n"
                f"This transcript contains all messages, files, and interactions from your support session. "
                f"Keep this for your records or future reference.\n\n"
                f"**Channel:** {channel_name}\n"
                f"**Generated:** <t:{int(datetime.now(timezone.utc).timestamp())}:F>"
            )
        )
        layout_view.add_item(container)

        await user.send(view=layout_view, file=file)
        logger.info(f"Enhanced transcript sent to {user.id}")
    except discord.Forbidden:
        logger.warning(f"Could not send transcript to {user.id} - DMs disabled")
    except Exception as e:
        logger.error(f"Failed to send transcript to {user.id}: {e}")


def format_time_ago(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    diff = now - dt

    if diff.days > 0:
        return f"{diff.days} day{'s' if diff.days != 1 else ''} ago"
    elif diff.seconds > 3600:
        hours = diff.seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    elif diff.seconds > 60:
        minutes = diff.seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    else:
        return "Just now"


def truncate_text(text: str, max_length: int = 100) -> str:
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def format_user_mention(user_id: int) -> str:
    return f"<@{user_id}>"


def format_channel_mention(channel_id: int) -> str:
    return f"<#{channel_id}>"


def format_role_mention(role_id: int) -> str:
    return f"<@&{role_id}>"


async def send_error_embed(interaction_or_ctx, title: str, description: str):
    from discord import ui

    layout_view = ui.LayoutView()
    container = ui.Container(accent_color=PURPLE_PRIMARY)
    container.add_item(ui.TextDisplay(f"### {resolve_emojis(title)}"))
    container.add_item(ui.Separator())
    container.add_item(ui.TextDisplay(resolve_emojis(description)))
    layout_view.add_item(container)

    try:
        if isinstance(interaction_or_ctx, discord.Interaction):
            if not interaction_or_ctx.response.is_done():
                await interaction_or_ctx.response.send_message(view=layout_view, ephemeral=True)
            else:
                await interaction_or_ctx.followup.send(view=layout_view, ephemeral=True)
        else:
            await interaction_or_ctx.send(view=layout_view)
    except Exception as e:
        logger.error(f"Error sending error embed: {e}")


async def send_success_embed(interaction_or_ctx, title: str, description: str):
    from discord import ui

    layout_view = ui.LayoutView()
    container = ui.Container(accent_color=PURPLE_PRIMARY)
    container.add_item(ui.TextDisplay(f"### {resolve_emojis(title)}"))
    container.add_item(ui.Separator())
    container.add_item(ui.TextDisplay(resolve_emojis(description)))
    layout_view.add_item(container)

    try:
        if isinstance(interaction_or_ctx, discord.Interaction):
            if not interaction_or_ctx.response.is_done():
                await interaction_or_ctx.response.send_message(view=layout_view, ephemeral=True)
            else:
                await interaction_or_ctx.followup.send(view=layout_view, ephemeral=True)
        else:
            await interaction_or_ctx.send(view=layout_view)
    except Exception as e:
        logger.error(f"Error sending success embed: {e}")


def strip_emojis(text: str) -> str:
    """Remove Discord custom emojis and unicode emojis from text"""
    text = re.sub(r"<a?:[a-zA-Z0-9_]+:\d+>", "", text)
    text = re.sub(r"[\U0001F300-\U0001F9FF\U00002600-\U000026FF\U00002700-\U000027BF]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def send_ephemeral_container(interaction_or_ctx, message: str):
    """Send an ephemeral message using Components v2 container
    Supports both discord.Interaction and commands.Context
    Note: For messages with interactive views (buttons/selects), use legacy embed pattern
    """
    from discord import ui

    layout_view = ui.LayoutView()
    container = ui.Container(accent_color=PURPLE_PRIMARY)
    container.add_item(ui.TextDisplay(resolve_emojis(message)))
    layout_view.add_item(container)
    try:
        if isinstance(interaction_or_ctx, discord.Interaction):
            if not interaction_or_ctx.response.is_done():
                await interaction_or_ctx.response.send_message(view=layout_view, ephemeral=True)
            else:
                await interaction_or_ctx.followup.send(view=layout_view, ephemeral=True)
        else:
            await interaction_or_ctx.send(view=layout_view, ephemeral=True)
    except Exception as e:
        logger.error(f"Error sending ephemeral container: {e}")


async def send_ephemeral_embed_container(interaction_or_ctx, embed: discord.Embed):
    """Convert an embed to a minimal container format and send ephemerally
    Format: **Title** / Separator / Text (legacy custom emoji IDs are resolved)
    Note: For messages with interactive views (buttons/selects), use legacy embed pattern
    """
    from discord import ui

    layout_view = ui.LayoutView()
    container = ui.Container(accent_color=PURPLE_PRIMARY)

    title = resolve_emojis(embed.title) if embed.title else ""
    if title:
        container.add_item(ui.TextDisplay(f"**{title}**"))
        container.add_item(ui.Separator())

    description = resolve_emojis(embed.description) if embed.description else ""
    if description:
        container.add_item(ui.TextDisplay(description))

    for field in embed.fields:
        field_name = resolve_emojis(field.name) if field.name else ""
        field_value = resolve_emojis(field.value) if field.value else ""
        if field_name or field_value:
            field_text = f"**{field_name}**\n{field_value}" if field_name else field_value
            container.add_item(ui.TextDisplay(field_text))

    layout_view.add_item(container)

    try:
        if isinstance(interaction_or_ctx, discord.Interaction):
            if not interaction_or_ctx.response.is_done():
                await interaction_or_ctx.response.send_message(view=layout_view, ephemeral=True)
            else:
                await interaction_or_ctx.followup.send(view=layout_view, ephemeral=True)
        else:
            await interaction_or_ctx.send(view=layout_view, ephemeral=True)
    except Exception as e:
        logger.error(f"Error sending ephemeral embed container: {e}")


def create_container_view(title: str, content: str):
    """Create a clean CV2 container with title, separator, and content"""
    from discord import ui

    layout_view = ui.LayoutView()
    container = ui.Container(accent_color=PURPLE_PRIMARY)
    container.add_item(ui.TextDisplay(resolve_emojis(f"### {title}")))
    container.add_item(ui.Separator())
    container.add_item(ui.TextDisplay(resolve_emojis(content)))
    layout_view.add_item(container)
    return layout_view


async def send_container(
    interaction_or_ctx, title: str, content: str, ephemeral: bool = True, var_context: Optional[Dict[str, Any]] = None
):
    """Send a clean CV2 container with title, separator, and content

    Args:
        interaction_or_ctx: Discord interaction or context
        title: Container title
        content: Container content
        ephemeral: Whether to send as ephemeral message
        var_context: Optional context for variable replacement
    """
    # Replace variables if context provided
    if var_context:
        title = replace_variables(title, var_context)
        content = replace_variables(content, var_context)

    view = create_container_view(title, content)
    try:
        if isinstance(interaction_or_ctx, discord.Interaction):
            if not interaction_or_ctx.response.is_done():
                await interaction_or_ctx.response.send_message(view=view, ephemeral=ephemeral)
            else:
                await interaction_or_ctx.followup.send(view=view, ephemeral=ephemeral)
        else:
            await interaction_or_ctx.send(view=view, ephemeral=ephemeral)
    except Exception as e:
        logger.error(f"Error sending container: {e}")


async def send_channel_container(channel, title: str, content: str, var_context: Optional[Dict[str, Any]] = None):
    """Send a clean CV2 container to a channel

    Args:
        channel: Discord channel
        title: Container title
        content: Container content
        var_context: Optional context for variable replacement
    """
    # Replace variables if context provided
    if var_context:
        title = replace_variables(title, var_context)
        content = replace_variables(content, var_context)

    view = create_container_view(title, content)
    try:
        await channel.send(view=view)
    except Exception as e:
        logger.error(f"Error sending channel container: {e}")


async def create_ticket_channel(bot, guild, creator, category, subject, description, ticket_number):
    try:
        async with bot.db.cursor() as cur:
            await cur.execute("SELECT category_id, role_id, ping_role_id FROM tickets WHERE guild_id = ?", (guild.id,))
            result = await cur.fetchone()

            if not result:
                return None

            category_id, role_id, ping_role_id = result

        ticket_category = guild.get_channel(category_id) if category_id else None
        ticket_role = guild.get_role(role_id) if role_id else None
        ping_role = guild.get_role(ping_role_id) if ping_role_id else None

        # Create channel name: categoryname-username-ticketnumber
        # Sanitize category name and username for channel naming
        sanitized_category = sanitize_channel_name(category)
        sanitized_username = sanitize_channel_name(creator.display_name)
        channel_name = f"{sanitized_category}-{sanitized_username}-{ticket_number:04d}"

        # Ensure channel name doesn't exceed Discord's 100 character limit
        if len(channel_name) > 100:
            # Truncate username if needed
            max_username_length = 100 - len(sanitized_category) - len(f"-{ticket_number:04d}") - 1
            if max_username_length > 0:
                sanitized_username = sanitized_username[:max_username_length]
                channel_name = f"{sanitized_category}-{sanitized_username}-{ticket_number:04d}"
            else:
                # Fallback to old format if category name is too long
                channel_name = f"ticket-{ticket_number:04d}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            creator: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }

        if ticket_role:
            overwrites[ticket_role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, manage_messages=True
            )

        channel = await guild.create_text_channel(
            name=channel_name,
            category=ticket_category,
            overwrites=overwrites,
            topic=f"Support ticket for {creator.display_name} | {category} | {subject}",
        )

        async with bot.db.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO ticket_instances
                (guild_id, channel_id, creator_id, ticket_number, category, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'open', ?)
            """,
                (guild.id, channel.id, creator.id, ticket_number, category, "open", datetime.now()),
            )
            await bot.db.commit()

        # Build context for variable replacement
        var_context = build_ticket_context(
            user=creator,
            ticket_number=ticket_number,
            category=category,
            subject=subject,
            description=description,
            guild=guild,
            channel=channel,
        )

        from discord import ui

        layout_view = ui.LayoutView()
        container = ui.Container(accent_color=PURPLE_PRIMARY)

        # Use variables in the ticket message
        title = f"### Ticket #{ticket_number:04d}"
        content = (
            f"**Category:** {category}\n**Subject:** {subject}\n**Description:** {description}\n\n"
            f"**Creator:** {creator.mention}\n**Status:** Open\n\n"
            f"Our team will be with you shortly!"
        )

        # Replace variables in content
        content = replace_variables(content, var_context)

        container.add_item(ui.TextDisplay(title))
        container.add_item(ui.Separator())
        container.add_item(ui.TextDisplay(content))
        layout_view.add_item(container)

        ping_text = ""
        if ping_role:
            ping_text = f"{ping_role.mention}"

        await channel.send(content=ping_text, view=layout_view)

        return channel

    except Exception as e:
        logger.error(f"Error creating ticket channel: {e}")
        return None
