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

        product = conn.execute(
            "SELECT * FROM products WHERE id=?",
            (self.product_id,)
        ).fetchone()

        stock = conn.execute(
            """
            SELECT * FROM stock
            WHERE product_id=? AND sold=0
            ORDER BY id
            LIMIT 1
            """,
            (self.product_id,)
        ).fetchone()

        if not product:
            await interaction.response.send_message(
                "❌ Produto não encontrado.",
                ephemeral=True
            )
            conn.close()
            return

        if not stock:
            await interaction.response.send_message(
                "❌ Produto sem estoque.",
                ephemeral=True
            )
            conn.close()
            return

        order_id = secrets.token_hex(4).upper()

        conn.execute(
            """
            INSERT INTO orders
            (id, user_id, product_id, price_cents, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                order_id,
                interaction.user.id,
                product["id"],
                product["price_cents"],
                "PENDENTE"
            )
        )

        conn.commit()
        conn.close()

        embed = discord.Embed(
            title="🧾 Pedido criado",
            color=discord.Color.blurple()
        )

        embed.add_field(
            name="Pedido",
            value=f"`{order_id}`",
            inline=False
        )

        embed.add_field(
            name="Produto",
            value=product["name"],
            inline=False
        )

        embed.add_field(
            name="Valor",
            value=money(product["price_cents"]),
            inline=False
        )

        embed.add_field(
            name="💠 PIX",
            value=(
                "Copie a chave abaixo e faça o pagamento:\n"
                f"`{PIX_KEY}`\n\n"
                "Depois envie o comprovante para um administrador."
            ),
            inline=False
        )

        embed.set_footer(
            text=f"{STORE_NAME} • Confirmação manual"
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )


@bot.event
async def on_ready():

    db().close()

    try:
        synced = await bot.tree.sync()

        print(
            f"Online como {bot.user}. "
            f"{len(synced)} comandos sincronizados."
        )

    except Exception as error:
        print(
            "Erro ao sincronizar comandos:",
            error
        )


@bot.tree.command(
    name="produtos",
    description="Mostra os produtos disponíveis"
)
async def produtos(interaction: discord.Interaction):

    conn = db()

    products = conn.execute(
        "SELECT * FROM products ORDER BY id"
    ).fetchall()

    conn.close()

    if not products:
        await interaction.response.send_message(
            "❌ Nenhum produto cadastrado."
        )
        return

    for product in products:

        conn = db()

        quantity = conn.execute(
            """
            SELECT COUNT(*)
            FROM stock
            WHERE product_id=? AND sold=0
            """,
            (product["id"],)
        ).fetchone()[0]

        conn.close()

        embed = discord.Embed(
            title=product["name"],
            description=(
                product["description"]
                or "Sem descrição."
            ),
            color=discord.Color.blurple()
        )

        embed.add_field(
            name="💰 Preço",
            value=money(product["price_cents"]),
            inline=True
        )

        embed.add_field(
            name="📦 Estoque",
            value=str(quantity),
            inline=True
        )

        await interaction.channel.send(
            embed=embed,
            view=BuyView(product["id"])
        )

    await interaction.response.send_message(
        "🛍️ Produtos enviados.",
        ephemeral=True
    )


@bot.tree.command(
    name="meuspedidos",
    description="Mostra seus pedidos"
)
async def meuspedidos(interaction: discord.Interaction):

    conn = db()

    rows = conn.execute(
        """
        SELECT o.*, p.name
        FROM orders o
        JOIN products p ON p.id=o.product_id
        WHERE o.user_id=?
        ORDER BY o.created_at DESC
        LIMIT 10
        """,
        (interaction.user.id,)
    ).fetchall()

    conn.close()

    if not rows:
        await interaction.response.send_message(
            "Você ainda não possui pedidos.",
            ephemeral=True
        )
        return

    text = "\n".join(
        f"`{row['id']}` • "
        f"{row['name']} • "
        f"{money(row['price_cents'])} • "
        f"**{row['status']}**"
        for row in rows
    )

    await interaction.response.send_message(
        text,
        ephemeral=True
    )


@bot.tree.command(
    name="addproduto",
    description="Admin: cadastra um produto"
)
@app_commands.describe(
    nome="Nome do produto",
    preco="Preço em reais. Exemplo: 9.90",
    descricao="Descrição do produto"
)
async def addproduto(
    interaction: discord.Interaction,
    nome: str,
    preco: float,
    descricao: str = ""
):

    if not is_admin(interaction):
        await interaction.response.send_message(
            "❌ Sem permissão.",
            ephemeral=True
        )
        return

    cents = round(preco * 100)

    conn = db()

    cursor = conn.execute(
        """
        INSERT INTO products
        (name, price_cents, description)
        VALUES (?, ?, ?)
        """,
        (nome, cents, descricao)
    )

    conn.commit()

    product_id = cursor.lastrowid

    conn.close()

    await interaction.response.send_message(
        f"✅ Produto criado com ID `{product_id}`."
    )


@bot.tree.command(
    name="addestoque",
    description="Admin: adiciona um item ao estoque"
)
@app_commands.describe(
    produto_id="ID do produto",
    item="Código, conta, chave ou texto entregue ao cliente"
)
async def addestoque(
    interaction: discord.Interaction,
    produto_id: int,
    item: str
):

    if not is_admin(interaction):
        await interaction.response.send_message(
            "❌ Sem permissão.",
            ephemeral=True
        )
        return

    conn = db()

    product = conn.execute(
        "SELECT id FROM products WHERE id=?",
        (produto_id,)
    ).fetchone()

    if not product:
        conn.close()

        await interaction.response.send_message(
            "❌ Produto não encontrado.",
            ephemeral=True
        )
        return

    conn.execute(
        """
        INSERT INTO stock
        (product_id, item)
        VALUES (?, ?)
        """,
        (produto_id, item)
    )

    conn.commit()
    conn.close()

    await interaction.response.send_message(
        "✅ Item adicionado ao estoque.",
        ephemeral=True
    )


@bot.tree.command(
    name="aprovar",
    description="Admin: aprova pedido e entrega o produto"
)
@app_commands.describe(
    pedido="ID do pedido"
)
async def aprovar(
    interaction: discord.Interaction,
    pedido: str
):

    if not is_admin(interaction):
        await interaction.response.send_message(
            "❌ Sem permissão.",
            ephemeral=True
        )
        return

    conn = db()

    order = conn.execute(
        """
        SELECT *
        FROM orders
        WHERE id=? AND status='PENDENTE'
        """,
        (pedido.upper(),)
    ).fetchone()

    if not order:
        conn.close()

        await interaction.response.send_message(
            "❌ Pedido não encontrado ou já processado.",
            ephemeral=True
        )
        return

    stock = conn.execute(
        """
        SELECT *
        FROM stock
        WHERE product_id=? AND sold=0
        ORDER BY id
        LIMIT 1
        """,
        (order["product_id"],)
    ).fetchone()

    if not stock:
        conn.close()

        await interaction.response.send_message(
            "❌ Sem estoque para entregar.",
            ephemeral=True
        )
        return

    conn.execute(
        "UPDATE stock SET sold=1 WHERE id=?",
        (stock["id"],)
    )

    conn.execute(
        "UPDATE orders SET status='APROVADO' WHERE id=?",
        (order["id"],)
    )

    conn.commit()
    conn.close()

    user = (
        bot.get_user(order["user_id"])
        or await bot.fetch_user(order["user_id"])
    )

    try:

        await user.send(
            "✅ **Pagamento confirmado!**\n\n"
            f"Pedido: `{order['id']}`\n\n"
            "Seu produto:\n"
            f"```{stock['item']}```\n\n"
            f"Obrigado por comprar na **{STORE_NAME}**!"
        )

        await interaction.response.send_message(
            "✅ Pedido aprovado e produto enviado por DM."
        )

    except discord.Forbidden:

        await interaction.response.send_message(
            "⚠️ Pedido aprovado, mas não consegui "
            "enviar DM. O cliente precisa permitir "
            "mensagens diretas."
        )


@bot.tree.command(
    name="cancelar",
    description="Admin: cancela um pedido pendente"
)
@app_commands.describe(
    pedido="ID do pedido"
)
async def cancelar(
    interaction: discord.Interaction,
    pedido: str
):

    if not is_admin(interaction):
        await interaction.response.send_message(
            "❌ Sem permissão.",
            ephemeral=True
        )
        return

    conn = db()

    cursor = conn.execute(
        """
        UPDATE orders
        SET status='CANCELADO'
        WHERE id=? AND status='PENDENTE'
        """,
        (pedido.upper(),)
    )

    conn.commit()
    conn.close()

    if cursor.rowcount:
        message = "✅ Pedido cancelado."
    else:
        message = (
            "❌ Pedido não encontrado "
            "ou já processado."
        )

    await interaction.response.send_message(
        message
    )


if not TOKEN:
    raise RuntimeError(
        "Defina DISCORD_TOKEN nas variáveis de ambiente."
    )


bot.run(TOKEN)
