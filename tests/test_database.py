import unittest

import aiosqlite

from utils.database import add_or_update_ticket_config, initialize_database
from utils.helpers import check_rate_limit, set_rate_limit
from utils.theme import PURPLE_PRIMARY


class FakeBot:
    def __init__(self, db):
        self.db = db


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await aiosqlite.connect(":memory:")
        self.bot = FakeBot(self.db)

    async def asyncTearDown(self):
        await self.db.close()

    async def test_schema_is_idempotent_and_enables_foreign_keys(self):
        await initialize_database(self.bot)
        await initialize_database(self.bot)

        async with self.db.execute("PRAGMA foreign_keys") as cursor:
            self.assertEqual((await cursor.fetchone())[0], 1)
        async with self.db.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cursor:
            tables = {row[0] for row in await cursor.fetchall()}
        self.assertTrue(
            {
                "tickets",
                "ticket_instances",
                "ticket_categories",
                "ticket_reminders",
                "rate_limits",
            }.issubset(tables)
        )

    async def test_partial_config_update_preserves_existing_values(self):
        await initialize_database(self.bot)
        self.assertTrue(
            await add_or_update_ticket_config(
                self.bot,
                123,
                channel_id=10,
                role_id=20,
                ticket_limit=5,
            )
        )
        self.assertTrue(await add_or_update_ticket_config(self.bot, 123, panel_type="button"))

        async with self.db.execute(
            """
            SELECT channel_id, role_id, ticket_limit, panel_type
            FROM tickets WHERE guild_id = 123
            """
        ) as cursor:
            row = await cursor.fetchone()
        self.assertEqual(row, (10, 20, 5, "button"))

    async def test_purple_default_migrates_only_historical_theme_colors(self):
        await initialize_database(self.bot)
        await self.db.executemany(
            "INSERT INTO tickets (guild_id, embed_color) VALUES (?, ?)",
            ((100, 54527), (200, 0x123456)),
        )
        await self.db.execute("INSERT INTO tickets (guild_id) VALUES (300)")
        await self.db.commit()

        await initialize_database(self.bot)

        async with self.db.execute("SELECT guild_id, embed_color FROM tickets ORDER BY guild_id") as cursor:
            rows = await cursor.fetchall()
        self.assertEqual(rows, [(100, PURPLE_PRIMARY), (200, 0x123456), (300, PURPLE_PRIMARY)])

    async def test_legacy_rate_limits_are_migrated(self):
        await self.db.execute("CREATE TABLE rate_limits (user_id INTEGER PRIMARY KEY, last_ticket_time REAL)")
        await self.db.execute("INSERT INTO rate_limits (user_id, last_ticket_time) VALUES (42, 123.5)")
        await self.db.commit()

        await initialize_database(self.bot)

        async with self.db.execute("SELECT guild_id, user_id, last_ticket_time FROM rate_limits") as cursor:
            self.assertEqual(await cursor.fetchone(), (0, 42, 123.5))
        async with self.db.execute("PRAGMA table_info(rate_limits)") as cursor:
            info = await cursor.fetchall()
        primary_key = [row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]]
        self.assertEqual(primary_key, ["guild_id", "user_id"])

    async def test_rate_limits_are_isolated_per_guild(self):
        await initialize_database(self.bot)
        await set_rate_limit(self.bot, 100, 42)

        self.assertTrue(await check_rate_limit(self.bot, 100, 42))
        self.assertFalse(await check_rate_limit(self.bot, 200, 42))

if __name__ == "__main__":
    unittest.main()
