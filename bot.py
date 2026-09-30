import os
import sqlite3
import secrets
from datetime import timedelta
import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ROLE_ID = int(os.getenv("ADMIN_ROLE_ID", "0"))
STORE_CATEGORY_ID = int(os.getenv("STORE_CATEGORY_ID", "0"))
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
            description TEXT DEFAULT '',
            banner_url TEXT DEFAULT '',
            channel_id INTEGER
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
            quantity INTEGER DEFAULT 1,
            channel_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migrations for databases created by older versions.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
    if "banner_url" not in columns:
        conn.execute("ALTER TABLE products ADD COLUMN banner_url TEXT DEFAULT ''")
    if "channel_id" not in columns:
        conn.execute("ALTER TABLE products ADD COLUMN channel_id INTEGER")

    columns = {row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}
    if "quantity" not in columns:
        conn.execute("ALTER TABLE orders ADD COLUMN quantity INTEGER DEFAULT 1")
    if "channel_id" not in columns:
        conn.execute("ALTER TABLE orders ADD COLUMN channel_id INTEGER")

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


def product_embed(product, stock_count):
    embed = discord.Embed(
        title=f"🛒 {product['name']}",
        description=product["description"] or "Sem descrição.",
        color=discord.Color.green()
    )

    if product["banner_url"]:
        embed.set_image(url=product["banner_url"])

    embed.add_field(
        name="💰 Preço",
        value=money(product["price_cents"]),
        inline=True
    )
    embed.add_field(
        name="📦 Estoque",
        value=str(stock_count),
        inline=True
    )
    embed.add_field(
        name="🛍️ Como comprar",
        value="Selecione a quantidade abaixo e clique em **Comprar**.",
        inline=False
    )
    embed.set_footer(text=f"{STORE_NAME} • Entrega após confirmação do pagamento")
    return embed


async def get_stock_count(product_id):
    conn = db()
    count = conn.execute(
        "SELECT COUNT(*) FROM stock WHERE product_id=? AND sold=0",
        (product_id,)
    ).fetchone()[0]
    conn.close()
    return count


async def refresh_product_message(channel, product_id):
    """Rebuild the product panel in a channel when an admin asks for it."""
    conn = db()
    product = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    conn.close()
    if not product or not channel:
        return

    stock_count = await get_stock_count(product_id)
    await channel.send(embed=product_embed(product, stock_count), view=ProductView(product_id))


class ConfirmBuyView(discord.ui.View):
    def __init__(self, product_id, quantity):
        super().__init__(timeout=120)
        self.product_id = product_id
        self.quantity = quantity

    @discord.ui.button(label="Confirmar compra", style=discord.ButtonStyle.success, emoji="🛒")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await create_order_channel(interaction, self.product_id, self.quantity)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Compra cancelada.", embed=None, view=None)


class ProductView(discord.ui.View):
    def __init__(self, product_id):
        super().__init__(timeout=None)
        self.product_id = product_id

        select = discord.ui.Select(
            placeholder="Selecione a quantidade",
            min_values=1,
            max_values=1,
            custom_id=f"product_qty:{product_id}",
            options=[
                discord.SelectOption(label=str(i), value=str(i), description=f"Comprar {i} unidade{'s' if i != 1 else ''}")
                for i in range(1, 11)
            ]
        )
        select.callback = self.select_quantity
        self.add_item(select)

    async def select_quantity(self, interaction: discord.Interaction):
        quantity = int(interaction.data["values"][0])

        stock_count = await get_stock_count(self.product_id)
        if stock_count < quantity:
            await interaction.response.send_message(
                f"❌ Só existem **{stock_count}** unidade(s) em estoque.",
                ephemeral=True
            )
            return

        conn = db()
        product = conn.execute("SELECT * FROM products WHERE id=?", (self.product_id,)).fetchone()
        conn.close()

        if not product:
            await interaction.response.send_message("❌ Produto não encontrado.", ephemeral=True)
            return

        total = product["price_cents"] * quantity
        embed = discord.Embed(
            title="🧾 Confirmar compra",
            description=f"**{product['name']}**\nQuantidade: **{quantity}**\nTotal: **{money(total)}**",
            color=discord.Color.blurple()
        )
        await interaction.response.send_message(
            embed=embed,
            view=ConfirmBuyView(self.product_id, quantity),
            ephemeral=True
        )


class OrderAdminView(discord.ui.View):
    def __init__(self, order_id):
        super().__init__(timeout=None)
        self.order_id = order_id

    @discord.ui.button(label="Aprovar pagamento", style=discord.ButtonStyle.success, emoji="✅", custom_id=f"order_approve:{order_id}")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
            return
        await approve_order(interaction, self.order_id)

    @discord.ui.button(label="Cancelar pedido", style=discord.ButtonStyle.danger, emoji="❌", custom_id=f"order_cancel:{order_id}")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_admin(interaction):
            await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
            return
        await cancel_order(interaction, self.order_id)


async def create_order_channel(interaction: discord.Interaction, product_id: int, quantity: int):
    if not interaction.guild:
        await interaction.response.send_message("❌ A compra precisa ser feita dentro de um servidor.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    conn = db()
    product = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    stock_count = conn.execute(
        "SELECT COUNT(*) FROM stock WHERE product_id=? AND sold=0", (product_id,)
    ).fetchone()[0]

    if not product:
        conn.close()
        await interaction.followup.send("❌ Produto não encontrado.", ephemeral=True)
        return

    if stock_count < quantity:
        conn.close()
        await interaction.followup.send(
            f"❌ Estoque insuficiente. Disponível: **{stock_count}**.",
            ephemeral=True
        )
        return

    order_id = secrets.token_hex(4).upper()
    total = product["price_cents"] * quantity

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            read_message_history=True
        ),
    }

    if ADMIN_ROLE_ID:
        role = interaction.guild.get_role(ADMIN_ROLE_ID)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

    category = interaction.guild.get_channel(STORE_CATEGORY_ID) if STORE_CATEGORY_ID else None
    if category and not isinstance(category, discord.CategoryChannel):
        category = None

    try:
        channel = await interaction.guild.create_text_channel(
            name=f"pedido-{order_id.lower()}",
            category=category,
            overwrites=overwrites,
            topic=f"Pedido {order_id} • {product['name']} • {interaction.user.id}"
        )
    except discord.Forbidden:
        conn.close()
        await interaction.followup.send(
            "❌ Não consegui criar o canal do pedido. Dê ao bot **Gerenciar Canais** e as permissões de canal necessárias.",
            ephemeral=True
        )
        return

    conn.execute(
        """
        INSERT INTO orders (id, user_id, product_id, price_cents, status, quantity, channel_id)
        VALUES (?, ?, ?, ?, 'PENDENTE', ?, ?)
        """,
        (order_id, interaction.user.id, product_id, total, quantity, channel.id)
    )
    conn.commit()
    conn.close()

    embed = discord.Embed(
        title="🧾 Pedido aguardando pagamento",
        description="Envie o comprovante neste canal após realizar o PIX.",
        color=discord.Color.blurple()
    )
    if product["banner_url"]:
        embed.set_image(url=product["banner_url"])

    embed.add_field(name="👤 Cliente", value=interaction.user.mention, inline=False)
    embed.add_field(name="📦 Produto", value=product["name"], inline=True)
    embed.add_field(name="🔢 Quantidade", value=str(quantity), inline=True)
    embed.add_field(name="💰 Total", value=money(total), inline=True)
    embed.add_field(
        name="💠 PIX",
        value=f"Copie a chave abaixo e faça o pagamento:\n`{PIX_KEY}`",
        inline=False
    )
    embed.set_footer(text=f"Pedido {order_id} • {STORE_NAME}")

    await channel.send(content=interaction.user.mention, embed=embed, view=OrderAdminView(order_id))
    await interaction.followup.send(f"✅ Seu pedido foi criado: {channel.mention}", ephemeral=True)


async def approve_order(interaction: discord.Interaction, order_id: str):
    await interaction.response.defer(ephemeral=True)

    conn = db()
    order = conn.execute(
        "SELECT * FROM orders WHERE id=? AND status='PENDENTE'", (order_id.upper(),)
    ).fetchone()

    if not order:
        conn.close()
        await interaction.followup.send("❌ Pedido não encontrado ou já processado.", ephemeral=True)
        return

    stocks = conn.execute(
        "SELECT * FROM stock WHERE product_id=? AND sold=0 ORDER BY id LIMIT ?",
        (order["product_id"], order["quantity"])
    ).fetchall()

    if len(stocks) < order["quantity"]:
        conn.close()
        await interaction.followup.send("❌ Não há estoque suficiente para entregar este pedido.", ephemeral=True)
        return

    for stock in stocks:
        conn.execute("UPDATE stock SET sold=1 WHERE id=?", (stock["id"],))

    conn.execute("UPDATE orders SET status='APROVADO' WHERE id=?", (order["id"],))
    conn.commit()
    conn.close()

    user = bot.get_user(order["user_id"]) or await bot.fetch_user(order["user_id"])
    items_text = "\n".join(f"{i + 1}. {stock['item']}" for i, stock in enumerate(stocks))

    try:
        await user.send(
            "✅ **Pagamento confirmado!**\n\n"
            f"Pedido: `{order['id']}`\n"
            f"Quantidade: **{order['quantity']}**\n\n"
            "Seu produto:\n"
            f"```\n{items_text}\n```\n"
            f"Obrigado por comprar na **{STORE_NAME}**!"
        )
        await interaction.followup.send("✅ Pagamento aprovado e produtos enviados por DM.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ Pagamento aprovado, mas a DM do cliente está fechada. Os itens foram marcados como entregues.",
            ephemeral=True
        )

    channel = interaction.guild.get_channel(order["channel_id"]) if interaction.guild and order["channel_id"] else None
    if channel:
        try:
            await channel.send("✅ **Pagamento aprovado!** Os produtos foram enviados por DM. Este canal será fechado em 10 segundos.")
            await discord.utils.sleep_until(discord.utils.utcnow() + timedelta(seconds=10))
            await channel.delete(reason=f"Pedido {order['id']} concluído")
        except (discord.Forbidden, discord.NotFound):
            pass


async def cancel_order(interaction: discord.Interaction, order_id: str):
    await interaction.response.defer(ephemeral=True)

    conn = db()
    cursor = conn.execute(
        "UPDATE orders SET status='CANCELADO' WHERE id=? AND status='PENDENTE'",
        (order_id.upper(),)
    )
    order = conn.execute("SELECT channel_id FROM orders WHERE id=?", (order_id.upper(),)).fetchone()
    conn.commit()
    conn.close()

    if not cursor.rowcount:
        await interaction.followup.send("❌ Pedido não encontrado ou já processado.", ephemeral=True)
        return

    await interaction.followup.send("✅ Pedido cancelado.", ephemeral=True)

    if interaction.guild and order and order["channel_id"]:
        channel = interaction.guild.get_channel(order["channel_id"])
        if channel:
            try:
                await channel.send("❌ **Pedido cancelado.** Este canal será fechado em 5 segundos.")
                await discord.utils.sleep_until(discord.utils.utcnow() + timedelta(seconds=5))
                await channel.delete(reason=f"Pedido {order_id} cancelado")
            except (discord.Forbidden, discord.NotFound):
                pass


@bot.event
async def on_ready():
    db().close()

    # Register persistent product panels after every restart.
    conn = db()
    products = conn.execute("SELECT id FROM products").fetchall()
    pending_orders = conn.execute("SELECT id FROM orders WHERE status='PENDENTE'").fetchall()
    conn.close()
    for product in products:
        bot.add_view(ProductView(product["id"]))
    for order in pending_orders:
        bot.add_view(OrderAdminView(order["id"]))

    try:
        synced = await bot.tree.sync()
        print(f"Online como {bot.user}. {len(synced)} comandos sincronizados.")
    except Exception as error:
        print("Erro ao sincronizar comandos:", error)


@bot.tree.command(name="addproduto", description="Admin: cria produto, canal e vitrine")
@app_commands.describe(
    nome="Nome do produto",
    preco="Preço unitário em reais. Exemplo: 9.90",
    descricao="Descrição do produto",
    banner="Banner do produto (anexe uma imagem)"
)
async def addproduto(
    interaction: discord.Interaction,
    nome: str,
    preco: float,
    descricao: str = "",
    banner: discord.Attachment | None = None
):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
        return

    if not interaction.guild:
        await interaction.response.send_message("❌ Use este comando dentro do servidor.", ephemeral=True)
        return

    if preco <= 0:
        await interaction.response.send_message("❌ O preço precisa ser maior que zero.", ephemeral=True)
        return

    if banner and not banner.content_type.startswith("image/"):
        await interaction.response.send_message("❌ O banner precisa ser uma imagem.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    banner_url = banner.url if banner else ""
    cents = round(preco * 100)

    conn = db()
    cursor = conn.execute(
        "INSERT INTO products (name, price_cents, description, banner_url) VALUES (?, ?, ?, ?)",
        (nome, cents, descricao, banner_url)
    )
    product_id = cursor.lastrowid
    conn.commit()
    conn.close()

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            add_reactions=False
        )
    }

    category = interaction.guild.get_channel(STORE_CATEGORY_ID) if STORE_CATEGORY_ID else None
    if category and not isinstance(category, discord.CategoryChannel):
        category = None

    try:
        channel = await interaction.guild.create_text_channel(
            name=f"{nome.lower().replace(' ', '-')[:80]}",
            category=category,
            overwrites=overwrites,
            topic=f"Vitrine do produto #{product_id}"
        )
    except discord.Forbidden:
        await interaction.followup.send(
            f"⚠️ Produto criado com ID `{product_id}`, mas não consegui criar o canal. Dê ao bot **Gerenciar Canais**.",
            ephemeral=True
        )
        return

    conn = db()
    conn.execute("UPDATE products SET channel_id=? WHERE id=?", (channel.id, product_id))
    conn.commit()
    product = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
    conn.close()

    await channel.send(embed=product_embed(product, 0), view=ProductView(product_id))
    await interaction.followup.send(f"✅ Produto criado e vitrine publicada em {channel.mention}.", ephemeral=True)


@bot.tree.command(name="addestoque", description="Admin: adiciona um item ao estoque")
@app_commands.describe(
    produto_id="ID do produto",
    item="Código, conta, chave ou texto entregue ao cliente"
)
async def addestoque(interaction: discord.Interaction, produto_id: int, item: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
        return

    conn = db()
    product = conn.execute("SELECT * FROM products WHERE id=?", (produto_id,)).fetchone()
    if not product:
        conn.close()
        await interaction.response.send_message("❌ Produto não encontrado.", ephemeral=True)
        return

    conn.execute("INSERT INTO stock (product_id, item) VALUES (?, ?)", (produto_id, item))
    conn.commit()
    conn.close()

    await interaction.response.send_message("✅ Item adicionado ao estoque.", ephemeral=True)


@bot.tree.command(name="meuspedidos", description="Mostra seus pedidos")
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
        await interaction.response.send_message("Você ainda não possui pedidos.", ephemeral=True)
        return

    text = "\n".join(
        f"`{row['id']}` • {row['name']} • {money(row['price_cents'])} • x{row['quantity']} • **{row['status']}**"
        for row in rows
    )
    await interaction.response.send_message(text, ephemeral=True)


@bot.tree.command(name="aprovar", description="Admin: aprova pedido e entrega os produtos")
@app_commands.describe(pedido="ID do pedido")
async def aprovar(interaction: discord.Interaction, pedido: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
        return
    await approve_order(interaction, pedido)


@bot.tree.command(name="cancelar", description="Admin: cancela um pedido pendente")
@app_commands.describe(pedido="ID do pedido")
async def cancelar(interaction: discord.Interaction, pedido: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Sem permissão.", ephemeral=True)
        return
    await cancel_order(interaction, pedido)


if not TOKEN:
    raise RuntimeError("Defina DISCORD_TOKEN nas variáveis de ambiente.")

bot.run(TOKEN)
