import os
import sqlite3
import secrets
import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))
PIX_KEY = os.getenv("PIX_KEY", "COLOQUE_SUA_CHAVE_PIX_AQUI")
STORE_NAME = os.getenv("STORE_NAME", "HIGH SYSTEM")

DB_PATH = "store.db"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price_cents INTEGER NOT NULL,
            description TEXT DEFAULT ''
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            item TEXT NOT NULL,
            sold INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            price_cents INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    return conn


def is_admin(interaction: discord.Interaction):
    if interaction.user.guild_permissions.administrator:
        return True

    if ADMIN_ROLE_ID:
        return any(
            role.id == ADMIN_ROLE_ID
            for role in getattr(interaction.user, "roles", [])
        )

    return False


def money(cents):
    return f"R$ {cents / 100:.2f}".replace(".", ",")


class BuyView(discord.ui.View):

    def __init__(self, product_id):
        super().__init__(timeout=None)
        self.product_id = product_id

    @discord.ui.button(
        label="Comprar",
        style=discord.ButtonStyle.success,
        emoji="🛒"
    )
    async def buy(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        conn = db()

        product
