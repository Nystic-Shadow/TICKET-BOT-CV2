import discord
import logging
from utils.helpers import send_ephemeral_container
from utils.application_emojis import resolve_component_emoji
from utils.theme import PURPLE_PRIMARY

logger = logging.getLogger("discord")


class TicketAuthorInfoSystem:
    """Advanced ticket author information system"""

    def __init__(self, bot):
        self.bot = bot

    async def get_user_info(self, guild, user_id, fetch_from_api=True):
        """Get comprehensive user information - DIRECT guild member check first"""
        try:
            member = guild.get_member(user_id)

            if member:
                logger.info(
                    f"<:j_icons_Correct:1382701297987485706> Direct guild lookup found member {user_id} in server {guild.id}"
                )
                return await self._get_member_info(member)

            logger.info(f"🔄 Member {user_id} not in cache, forcing guild chunk for {guild.id}")
            try:
                if not guild.chunked:
                    await guild.chunk()
                    logger.info(f"📥 Guild {guild.id} chunked successfully")

                member = guild.get_member(user_id)
                if member:
                    logger.info(
                        f"<:j_icons_Correct:1382701297987485706> Found member {user_id} after chunking guild {guild.id}"
                    )
                    return await self._get_member_info(member)
                else:
                    logger.info(
                        f"<:icons_Wrong:1382701332955402341> Member {user_id} definitively not in guild {guild.id} after chunking"
                    )

            except Exception as chunk_error:
                logger.warning(f"<:warning:1382701413284446228> Failed to chunk guild {guild.id}: {chunk_error}")

            logger.info(f"🔍 Performing manual member search for {user_id} in guild {guild.id}")
            for member in guild.members:
                if member.id == user_id:
                    logger.info(
                        f"<:j_icons_Correct:1382701297987485706> Manual search found member {user_id} in guild {guild.id}"
                    )
                    return await self._get_member_info(member)

            logger.info(
                f"<:icons_Wrong:1382701332955402341> User {user_id} confirmed NOT in server {guild.id} - trying API"
            )
            if fetch_from_api:
                try:
                    user = await self.bot.fetch_user(user_id)
                    if user:
                        logger.info(f"📡 API fetch successful for user {user_id} - user left server")
                        return await self._get_left_user_info(user, guild)
                except discord.NotFound:
                    logger.info(f"<:Icons_Trash:1382703995700645969> User {user_id} account deleted")
                    return await self._get_deleted_user_info(user_id)
                except discord.HTTPException as e:
                    logger.warning(f"<:warning:1382701413284446228> HTTP error fetching user {user_id}: {e}")
                    return await self._get_unknown_user_info(user_id)

            logger.info(f"❓ Could not determine status of user {user_id}")
            return await self._get_unknown_user_info(user_id)

        except Exception as e:
            logger.error(f"<:icons_Wrong:1382701332955402341> Error getting user info for {user_id}: {e}")
            return await self._get_error_info(user_id, str(e))

    async def _get_member_info(self, member):
        """Get detailed information for current guild member"""
        return {
            "type": "member",
            "user": member,
            "id": member.id,
            "name": member.name,
            "display_name": member.display_name,
            "mention": member.mention,
            "avatar_url": member.display_avatar.url,
            "joined_at": member.joined_at,
            "created_at": member.created_at,
            "status": member.status,
            "activity": member.activity,
            "roles": [role for role in member.roles if role != member.guild.default_role],
            "permissions": member.guild_permissions,
            "is_bot": member.bot,
            "is_system": member.system,
            "premium_since": getattr(member, "premium_since", None),
            "pending": getattr(member, "pending", False),
            "timed_out_until": getattr(member, "timed_out_until", None),
            "in_server": True,
        }

    async def _get_left_user_info(self, user, guild):
        """Get information for user who left the guild"""
        return {
            "type": "left_user",
            "user": user,
            "id": user.id,
            "name": user.name,
            "display_name": user.display_name,
            "mention": user.mention,
            "avatar_url": user.display_avatar.url,
            "created_at": user.created_at,
            "is_bot": user.bot,
            "is_system": user.system,
            "in_server": False,
            "left_guild": True,
        }

    async def _get_deleted_user_info(self, user_id):
        """Get information for deleted user account"""
        return {
            "type": "deleted",
            "id": user_id,
            "mention": f"<@{user_id}>",
            "in_server": False,
            "account_deleted": True,
        }

    async def _get_unknown_user_info(self, user_id):
        """Get minimal information for unknown user"""
        return {"type": "unknown", "id": user_id, "mention": f"<@{user_id}>", "in_server": False, "unknown": True}

    async def _get_error_info(self, user_id, error):
        """Get error information"""
        return {"type": "error", "id": user_id, "mention": f"<@{user_id}>", "error": error}

    def create_user_info_container(self, user_info):
        """Create container view with user information"""
        if user_info["type"] == "member":
            return self._create_member_container(user_info)
        elif user_info["type"] == "left_user":
            return self._create_left_user_container(user_info)
        elif user_info["type"] == "deleted":
            return self._create_deleted_container(user_info)
        elif user_info["type"] == "unknown":
            return self._create_unknown_container(user_info)
        else:
            return self._create_error_container(user_info)

    def _create_member_container(self, info):
        """Create container for current guild member"""
        from discord import ui

        perms = info["permissions"]
        key_perms = []
        perm_checks = [
            ("kick_members", "Kick Members"),
            ("ban_members", "Ban Members"),
            ("administrator", "Administrator"),
            ("manage_channels", "Manage Channels"),
            ("manage_messages", "Manage Messages"),
            ("moderate_members", "Moderate Members"),
        ]

        for perm_attr, perm_name in perm_checks:
            if getattr(perms, perm_attr, False):
                key_perms.append(perm_name)

        layout_view = ui.LayoutView()
        container = ui.Container(accent_color=PURPLE_PRIMARY)
        container.add_item(ui.TextDisplay(f"### Ticket Author: {info['name']}"))
        container.add_item(ui.Separator())

        content = f"**ID:** {info['id']} {info['mention']}\n\n"
        if info["joined_at"]:
            content += f"**Joined:** {info['joined_at'].strftime('%b %d, %Y')}\n"
        content += f"**Registered:** {info['created_at'].strftime('%b %d, %Y')}\n\n"

        if key_perms:
            content += f"**Permissions:** {', '.join(key_perms)}\n\n"

        if info["roles"]:
            top_roles = sorted(info["roles"], key=lambda r: r.position, reverse=True)[:5]
            content += f"**Top Roles:** {' '.join([role.mention for role in top_roles])}\n\n"

        content += "**Status:** Currently in Server"
        container.add_item(ui.TextDisplay(content))
        layout_view.add_item(container)

        return layout_view

    def _create_left_user_container(self, info):
        """Create container for user who left the server"""
        from discord import ui

        layout_view = ui.LayoutView()
        container = ui.Container(accent_color=PURPLE_PRIMARY)
        container.add_item(ui.TextDisplay(f"### Ticket Author: {info['name']}"))
        container.add_item(ui.Separator())
        container.add_item(
            ui.TextDisplay(
                f"**ID:** {info['id']} {info['mention']}\n\n"
                f"**Registered:** {info['created_at'].strftime('%b %d, %Y')}\n"
                f"**Account Type:** {'Bot' if info['is_bot'] else 'User'}\n\n"
                f"**Status:** Left Server"
            )
        )
        layout_view.add_item(container)

        return layout_view

    def _create_deleted_container(self, info):
        """Create container for deleted user account"""
        from discord import ui

        layout_view = ui.LayoutView()
        container = ui.Container(accent_color=PURPLE_PRIMARY)
        container.add_item(ui.TextDisplay("### Ticket Author Info"))
        container.add_item(ui.Separator())
        container.add_item(
            ui.TextDisplay(
                f"**ID:** {info['id']} {info['mention']}\n\n"
                f"**Status:** Account Deleted\n\n"
                f"This Discord account no longer exists."
            )
        )
        layout_view.add_item(container)

        return layout_view

    def _create_unknown_container(self, info):
        """Create container for unknown user"""
        from discord import ui

        layout_view = ui.LayoutView()
        container = ui.Container(accent_color=PURPLE_PRIMARY)
        container.add_item(ui.TextDisplay("### Ticket Author Info"))
        container.add_item(ui.Separator())
        container.add_item(
            ui.TextDisplay(
                f"**ID:** {info['id']} {info['mention']}\n\n"
                f"**Status:** Left Server\n\n"
                f"Could not fetch additional user information."
            )
        )
        layout_view.add_item(container)

        return layout_view

    def _create_error_container(self, info):
        """Create container for error case"""
        from discord import ui

        layout_view = ui.LayoutView()
        container = ui.Container(accent_color=PURPLE_PRIMARY)
        container.add_item(ui.TextDisplay("### Ticket Author Info"))
        container.add_item(ui.Separator())
        container.add_item(
            ui.TextDisplay(f"**Error:** {info.get('error', 'Unknown error')}\n**User ID:** {info['id']}")
        )
        layout_view.add_item(container)

        return layout_view

    def create_user_info_container_text(self, user_info):
        """Create container text with user information"""
        if user_info["type"] == "member":
            return self._create_member_container_text(user_info)
        elif user_info["type"] == "left_user":
            return self._create_left_user_container_text(user_info)
        elif user_info["type"] == "deleted":
            return self._create_deleted_container_text(user_info)
        elif user_info["type"] == "unknown":
            return self._create_unknown_container_text(user_info)
        else:
            return self._create_error_container_text(user_info)

    def _create_member_container_text(self, info):
        """Create container text for current guild member"""
        perms = info["permissions"]
        key_perms = []
        perm_checks = [
            ("kick_members", "Kick Members"),
            ("ban_members", "Ban Members"),
            ("administrator", "Administrator"),
            ("manage_channels", "Manage Channels"),
            ("manage_messages", "Manage Messages"),
            ("moderate_members", "Moderate Members"),
        ]

        for perm_attr, perm_name in perm_checks:
            if getattr(perms, perm_attr, False):
                key_perms.append(perm_name)

        content = f"# Ticket Author: {info['name']}\n\n"
        content += f"**ID:** {info['id']} {info['mention']}\n\n"

        if info["joined_at"]:
            content += f"**Joined:** {info['joined_at'].strftime('%b %d, %Y')}\n"
        content += f"**Registered:** {info['created_at'].strftime('%b %d, %Y')}\n\n"

        if key_perms:
            content += f"**Permissions:** {', '.join(key_perms)}\n\n"

        content += "**Status:** Currently in Server"
        return content

    def _create_left_user_container_text(self, info):
        """Create container text for user who left the server"""
        content = f"# Ticket Author: {info['name']}\n\n"
        content += f"**ID:** {info['id']} {info['mention']}\n\n"
        content += f"**Registered:** {info['created_at'].strftime('%b %d, %Y')}\n"
        content += f"**Account Type:** {'Bot' if info['is_bot'] else 'User'}\n\n"
        content += "**Status:** Left Server"
        return content

    def _create_deleted_container_text(self, info):
        """Create container text for deleted user account"""
        content = "# Ticket Author Info\n\n"
        content += f"**ID:** {info['id']} {info['mention']}\n\n"
        content += "**Status:** Account Deleted"
        return content

    def _create_unknown_container_text(self, info):
        """Create container text for unknown user"""
        content = "# Ticket Author Info\n\n"
        content += f"**ID:** {info['id']} {info['mention']}\n\n"
        content += "**Status:** Left Server (Unable to fetch details)"
        return content

    def _create_error_container_text(self, info):
        """Create container text for error case"""
        content = "# Ticket Author Info\n\n"
        content += f"**Error:** {info.get('error', 'Unknown error')}\n"
        content += f"**User ID:** {info['id']}"
        return content


class UserAvatarView(discord.ui.View):
    """Advanced avatar view with multiple options"""

    def __init__(self, user_info):
        super().__init__(timeout=300)
        self.user_info = user_info

    @discord.ui.button(
        label="View Avatar",
        style=discord.ButtonStyle.primary,
        emoji=resolve_component_emoji("<:icons_heart:1382705238619984005>"),
    )
    async def view_avatar(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            if self.user_info["type"] in ["member", "left_user"]:
                user = self.user_info["user"]

                avatar_formats = []
                base_url = str(user.display_avatar.url).split("?")[0]

                formats = ["png", "jpg", "webp"]
                if user.display_avatar.is_animated():
                    formats.insert(0, "gif")

                for fmt in formats:
                    avatar_formats.append(f"[{fmt.upper()}]({base_url}.{fmt}?size=1024)")

                from discord import ui

                layout_view = ui.LayoutView()
                container = ui.Container(accent_color=PURPLE_PRIMARY)
                container.add_item(ui.TextDisplay(f"### {self.user_info['display_name']}'s Avatar"))
                container.add_item(ui.Separator())
                container.add_item(ui.MediaGallery(discord.ui.MediaGalleryItem(media=self.user_info["avatar_url"])))
                container.add_item(ui.Separator())
                container.add_item(ui.TextDisplay(f"**Download:** {' | '.join(avatar_formats)}"))
                layout_view.add_item(container)

                if not interaction.response.is_done():
                    await interaction.response.send_message(view=layout_view, ephemeral=True)
                else:
                    await interaction.followup.send(view=layout_view, ephemeral=True)
            else:
                await send_ephemeral_container(interaction, "Avatar not available for this user")
        except Exception as e:
            logger.error(f"Error displaying avatar: {e}")
            await send_ephemeral_container(interaction, f"Error displaying avatar: {str(e)}")


class TicketClosedLogView(discord.ui.View):
    """Enhanced ticket closed log view with advanced author info"""

    def __init__(self, bot, ticket_data):
        super().__init__(timeout=None)
        self.bot = bot
        self.ticket_data = ticket_data
        self.author_system = TicketAuthorInfoSystem(bot)

    async def send_author_info_container(self, interaction: discord.Interaction):
        """Send author info as a properly structured container"""
        try:
            creator_id = self.ticket_data.get("creator_id")

            if not creator_id:
                layout = discord.ui.LayoutView()
                container = discord.ui.Container(accent_color=PURPLE_PRIMARY)
                container.add_item(discord.ui.TextDisplay("### Creator Not Found"))
                container.add_item(discord.ui.Separator())
                container.add_item(discord.ui.TextDisplay("Could not find the ticket creator ID."))
                layout.add_item(container)
                await interaction.followup.send(view=layout, ephemeral=True)
                return

            user_info = await self.author_system.get_user_info(interaction.guild, creator_id)

            layout = discord.ui.LayoutView()
            container = discord.ui.Container(accent_color=PURPLE_PRIMARY)

            if user_info["type"] == "member":
                container.add_item(discord.ui.TextDisplay(f"### Ticket Author: {user_info['name']}"))
                container.add_item(discord.ui.Separator())

                gallery = discord.ui.MediaGallery()
                gallery.add_item(media=user_info["avatar_url"])
                container.add_item(gallery)

                perms = user_info["permissions"]
                key_perms = []
                for perm_attr, perm_name in [
                    ("administrator", "Admin"),
                    ("ban_members", "Ban"),
                    ("kick_members", "Kick"),
                    ("manage_messages", "Manage Messages"),
                ]:
                    if getattr(perms, perm_attr, False):
                        key_perms.append(perm_name)

                content = f"**ID:** `{user_info['id']}` {user_info['mention']}\n"
                if user_info["joined_at"]:
                    content += f"**Joined:** `{user_info['joined_at'].strftime('%b %d, %Y')}`\n"
                content += f"**Registered:** `{user_info['created_at'].strftime('%b %d, %Y')}`\n"
                if key_perms:
                    content += f"**Permissions:** `{', '.join(key_perms)}`\n"
                content += "**Status:** `In Server`"
                container.add_item(discord.ui.TextDisplay(content))

            elif user_info["type"] == "left_user":
                container.add_item(discord.ui.TextDisplay(f"### Ticket Author: {user_info['name']}"))
                container.add_item(discord.ui.Separator())

                gallery = discord.ui.MediaGallery()
                gallery.add_item(media=user_info["avatar_url"])
                container.add_item(gallery)

                container.add_item(
                    discord.ui.TextDisplay(
                        f"**ID:** `{user_info['id']}` {user_info['mention']}\n"
                        f"**Registered:** `{user_info['created_at'].strftime('%b %d, %Y')}`\n"
                        f"**Status:** `Left Server`"
                    )
                )
            else:
                container.add_item(discord.ui.TextDisplay("### Ticket Author Info"))
                container.add_item(discord.ui.Separator())
                status = "Account Deleted" if user_info["type"] == "deleted" else "Unknown"
                container.add_item(
                    discord.ui.TextDisplay(
                        f"**ID:** `{user_info['id']}` {user_info['mention']}\n**Status:** `{status}`"
                    )
                )

            layout.add_item(container)
            await interaction.followup.send(view=layout, ephemeral=True)

        except Exception as e:
            logger.error(f"Error sending author info container: {e}")
            await send_ephemeral_container(interaction, f"### System Error\n\n{str(e)}")

    @discord.ui.button(
        label="Ticket Author Info",
        style=discord.ButtonStyle.secondary,
        emoji=resolve_component_emoji("<:id_icons:1384041001114407013>"),
        custom_id="advanced_ticket_author_info",
    )
    async def author_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
            await self.send_author_info_container(interaction)

        except Exception as e:
            logger.error(f"Error in advanced author info: {e}")
            await send_ephemeral_container(interaction, f"# System Error\n\n{str(e)}")
