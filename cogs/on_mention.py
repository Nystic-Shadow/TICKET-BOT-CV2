from discord.ext import commands
from discord import ui
import logging
from utils.config import config
from utils.application_emojis import resolve_emojis
from utils.theme import PURPLE_PRIMARY

logger = logging.getLogger("discord")


class OnMention(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = config

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        if (
            self.bot.user.mentioned_in(message)
            and not message.mention_everyone
            and not (
                message.reference and message.reference.resolved and message.reference.resolved.author == self.bot.user
            )
        ):
            try:
                view = ui.LayoutView()
                container = ui.Container(accent_color=PURPLE_PRIMARY)

                container.add_item(ui.TextDisplay(f"### {self.bot.user.name} Here :3"))

                container.add_item(ui.Separator())

                section = ui.Section(accessory=ui.Thumbnail(media=self.bot.user.display_avatar.url))
                section.add_item(
                    ui.TextDisplay(
                        resolve_emojis(
                            f"Hey there, {message.author.mention}\n"
                            f"<:welcome:1382706419765350480> **Welcome to the {self.bot.user.name} Support System!**"
                        )
                    )
                )
                container.add_item(section)

                if self.config.SUPPORT_SERVER:
                    container.add_item(ui.Separator())
                    container.add_item(
                        ui.TextDisplay(f"-# Need Assistance? [Join Support]({self.config.SUPPORT_SERVER})")
                    )

                view.add_item(container)

                await message.reply(view=view, mention_author=False)

                logger.info(
                    f"Sent mention response to {message.author} in {message.guild.name if message.guild else 'DM'}"
                )

            except Exception as e:
                logger.error(f"Error sending mention response: {e}")


async def setup(bot):
    await bot.add_cog(OnMention(bot))
