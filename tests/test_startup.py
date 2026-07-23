import unittest

from main import TicketBot
from utils.config import config


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_setup_loads_all_extensions_after_emoji_mapping(self):
        original_values = {
            "DATABASE_PATH": config.DATABASE_PATH,
            "TRIGGERS_DATABASE_PATH": config.TRIGGERS_DATABASE_PATH,
            "AUTO_SYNC_APPLICATION_EMOJIS": config.AUTO_SYNC_APPLICATION_EMOJIS,
        }
        object.__setattr__(config, "DATABASE_PATH", ":memory:")
        object.__setattr__(config, "TRIGGERS_DATABASE_PATH", ":memory:")
        object.__setattr__(config, "AUTO_SYNC_APPLICATION_EMOJIS", False)

        bot = TicketBot()
        try:
            await bot._async_setup_hook()
            await bot.setup_hook()

            self.assertEqual(len(bot.extensions), 5)
            self.assertGreaterEqual(len(list(bot.walk_commands())), 38)
            self.assertGreaterEqual(len(bot.tree.get_commands()), 20)
        finally:
            await bot.close()
            for name, value in original_values.items():
                object.__setattr__(config, name, value)


if __name__ == "__main__":
    unittest.main()
