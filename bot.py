import os
import asyncio
import logging
import json
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Button, View, Select

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("Переменная окружения DISCORD_TOKEN не установлена!")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# ========== НАСТРОЙКИ ==========
WHITE_SERVER_ID = 1458476750198800590
PANEL_CHANNEL_ID = 1495833214218928218

# ID категорий для каждого суда
COURT_CATEGORY_IDS = {
    "Областной Суд": 1459080376797630494,
    "Верховный Суд": 1458957161890582643,
    "Конституционный Суд": 1458957154244624456
}

ROLE_IDS = [
    1475470962379067392,
    1491509114034192384,
    1491508543432687666
]

lawsuit_status = {}

def check_roles(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    for role_id in ROLE_IDS:
        role = interaction.user.get_role(role_id)
        if role:
            return True
    return False

def can_close_lawsuit(interaction: discord.Interaction, lawsuit_author_id: int) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    for role_id in ROLE_IDS:
        role = interaction.user.get_role(role_id)
        if role:
            return True
    if interaction.user.id == lawsuit_author_id:
        return True
    return False

class LawsuitBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.synced = False
        self.config_file = "lawsuit_config.json"
        self.config = self.load_config()

    def load_config(self):
        if os.path.exists(self.config_file):
            with open(self.config_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"panel_channel_id": PANEL_CHANNEL_ID, "log_channel_id": None}

    def save_config(self):
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=2, ensure_ascii=False)

    async def setup_hook(self):
        await self.tree.sync()
        logger.info("Слэш-команды синхронизированы")
        self.add_view(LawsuitButton(self))

bot = LawsuitBot()

# ========== КНОПКА ВЫЗОВА МЕНЮ ВЫБОРА СУДА ==========
class LawsuitButton(View):
    def __init__(self, bot_instance):
        super().__init__(timeout=None)
        self.bot_instance = bot_instance

    @discord.ui.button(label="⚖️ Подать иск в суд", style=discord.ButtonStyle.green, custom_id="lawsuit_create")
    async def lawsuit_create(self, interaction: discord.Interaction, button: Button):
        if interaction.guild.id != WHITE_SERVER_ID:
            await interaction.response.send_message("⛔ Бот работает только на официальном сервере!", ephemeral=True)
            return
        
        view = CourtSelectView()
        await interaction.response.send_message("Выберите судебную инстанцию:", view=view, ephemeral=True)

# ========== ВЫПАДАЮЩЕЕ МЕНЮ ВЫБОРА СУДА ==========
class CourtSelectView(View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(CourtSelect())

class CourtSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Областной Суд", description="Подать иск в Областной суд", emoji="🏛️"),
            discord.SelectOption(label="Верховный Суд", description="Подать иск в Верховный суд", emoji="⚖️"),
            discord.SelectOption(label="Конституционный Суд", description="Подать иск в Конституционный суд", emoji="📜"),
        ]
        super().__init__(placeholder="Выберите суд для подачи иска...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        court_name = self.values[0]
        await interaction.response.defer(ephemeral=True)
        
        try:
            if interaction.guild.id != WHITE_SERVER_ID:
                await interaction.followup.send("⛔ Бот работает только на официальном сервере!", ephemeral=True)
                return
            
            category_id = COURT_CATEGORY_IDS.get(court_name)
            if not category_id:
                await interaction.followup.send(f"❌ Неизвестный суд: {court_name}", ephemeral=True)
                return
            
            category = interaction.guild.get_channel(category_id)
            if not category:
                # Если категория не найдена, пытаемся создать (на всякий случай)
                category = await interaction.guild.create_category(f"Иски - {court_name}")
                await interaction.followup.send(f"ℹ️ Категория для {court_name} создана автоматически.", ephemeral=True)
            
            short_court = court_name.replace(" ", "").replace("Суд", "")
            channel_name = f"иск-{interaction.user.name.lower()}-{short_court.lower()}"
            existing = discord.utils.get(interaction.guild.text_channels, name=channel_name)
            if existing:
                await interaction.followup.send("❌ У вас уже есть открытый иск! Дождитесь рассмотрения.", ephemeral=True)
                return
            
            overwrites = {
                interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, read_message_history=True),
                interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            
            for role_id in ROLE_IDS:
                role = interaction.guild.get_role(role_id)
                if role:
                    overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True, attach_files=True)
            
            ticket_channel = await category.create_text_channel(channel_name, overwrites=overwrites)
            
            lawsuit_status[ticket_channel.id] = {
                "status": "waiting",
                "author_id": interaction.user.id,
                "channel_name": channel_name,
                "court": court_name
            }
            
            embed = discord.Embed(
                title=f"⚖️ {court_name} Нижегородской области",
                description=(
                    "**Форма искового заявления**\n\n"
                    f"**Наименование судебной инстанции:** {court_name} Нижегородской области\n"
                    "г. Южный, Нижегородская область\n\n"
                    "**От:**\n"
                    f"Истец: {interaction.user.name}\n"
                    "Место службы/работы: __________\n"
                    "Номер паспорта: (CID) __________\n"
                    "Номер Телефона: __________\n\n"
                    "**К:**\n"
                    "Ответчик: __________\n"
                    "Место службы/работы: __________\n"
                    "Номер паспорта: (CID) __________\n\n"
                    "**ИСКОВОЕ ЗАЯВЛЕНИЕ**\n"
                    f"от {interaction.user.name} к __________\n"
                    "о __________\n\n"
                    f"Я, {interaction.user.name}, руководствуясь действующим законодательством Нижегородской области, "
                    "с целью восстановления своих прав и законных интересов подаю настоящее исковое заявление на __________ "
                    "с номером паспорта (CID) __________.\n"
                    "(опишите суть искового заявления)\n\n"
                    "Исходя из вышеизложенного прошу __________\n\n"
                    "**Приложения:**\n"
                    "1) Ксерокопия паспорта истца: (ссылка на скрин)\n"
                    "2) Вещественные доказательства: (в случае наличия)\n"
                    "3) Документ, подтверждающий уплату судебной пошлины: (ссылка на скрин)\n"
                    "4) Ходатайства: (в случае наличия)\n\n"
                    f"**Истец:** {interaction.user.name}\n"
                    f"**Дата подачи:** {datetime.now().strftime('%d.%m.%Y')}\n\n"
                    "**Статус:** 🟢 ОЖИДАЕТ РАССМОТРЕНИЯ"
                ),
                color=discord.Color.green()
            )
            embed.set_footer(text="by Ilya Vetrov")
            
            await ticket_channel.send(
                f"{interaction.user.mention}, **ваше исковое заявление зарегистрировано в {court_name}.**\n"
                "Заполните форму выше, заменив прочерки на свои данные.\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "**by Ilya Vetrov**",
                view=LawsuitControlButtons(interaction.user.id, ticket_channel.id)
            )
            await ticket_channel.send(embed=embed)
            
            await interaction.followup.send(f"✅ Иск подан в **{court_name}**!\nПерейдите в канал {ticket_channel.mention}\n\n**by Ilya Vetrov**", ephemeral=True)
            logger.info(f"Создан иск {channel_name} от {interaction.user} в {court_name}")
            
        except discord.Forbidden:
            await interaction.followup.send("❌ У бота нет прав для создания канала! Выдайте ему права `Manage Channels`.", ephemeral=True)
        except Exception as e:
            logger.error(f"Ошибка при создании иска: {e}")
            await interaction.followup.send(f"❌ Произошла ошибка: {str(e)[:100]}\n\nПожалуйста, сообщите администратору.\n\nby Ilya Vetrov", ephemeral=True)

# ========== КНОПКИ УПРАВЛЕНИЯ ИСКОМ ==========
class LawsuitControlButtons(View):
    def __init__(self, author_id, channel_id):
        super().__init__(timeout=None)
        self.author_id = author_id
        self.channel_id = channel_id

    @discord.ui.button(label="🔒 Закрыть иск", style=discord.ButtonStyle.red, custom_id="close_lawsuit")
    async def close_lawsuit(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        
        if not can_close_lawsuit(interaction, self.author_id):
            await interaction.followup.send("❌ У вас нет прав для закрытия этого иска!\n\n*Закрыть могут: автор, администратор или сотрудник с ролью*", ephemeral=True)
            return
        
        if interaction.user.id == self.author_id:
            if self.channel_id in lawsuit_status and lawsuit_status[self.channel_id].get("status") == "review":
                await interaction.followup.send("❌ Иск уже на рассмотрении! Вы не можете его закрыть.\n\n*Дождитесь решения сотрудника*", ephemeral=True)
                return
        
        await interaction.followup.send("🔒 Иск будет закрыт через 3 секунды...\n\nby Ilya Vetrov", ephemeral=True)
        
        os.makedirs("lawsuit_logs", exist_ok=True)
        log_file = f"lawsuit_logs/{interaction.channel.name}.txt"
        
        try:
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(f"Лог иска: {interaction.channel.name}\n")
                f.write(f"Дата закрытия: {datetime.utcnow()}\n")
                f.write(f"Закрыл: {interaction.user} ({interaction.user.id})\n")
                f.write("by Ilya Vetrov\n")
                f.write("="*50 + "\n")
                async for msg in interaction.channel.history(limit=500, oldest_first=True):
                    f.write(f"{msg.author} [{msg.created_at}]: {msg.content}\n")
        except Exception as e:
            logger.error(f"Ошибка сохранения лога: {e}")
        
        await asyncio.sleep(3)
        
        try:
            if self.channel_id in lawsuit_status:
                del lawsuit_status[self.channel_id]
            await interaction.channel.delete()
        except Exception as e:
            logger.error(f"Ошибка удаления канала: {e}")

    @discord.ui.button(label="📋 На рассмотрении", style=discord.ButtonStyle.primary, custom_id="review_lawsuit")
    async def review_lawsuit(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        
        if not check_roles(interaction):
            await interaction.followup.send("❌ Только сотрудники или администраторы могут перевести иск в режим рассмотрения!\n\nby Ilya Vetrov", ephemeral=True)
            return
        
        if self.channel_id in lawsuit_status and lawsuit_status[self.channel_id].get("status") == "review":
            await interaction.followup.send("ℹ️ Этот иск уже на рассмотрении!\n\nby Ilya Vetrov", ephemeral=True)
            return
        
        lawsuit_status[self.channel_id] = {
            "status": "review",
            "author_id": self.author_id,
            "channel_name": interaction.channel.name,
            "court": lawsuit_status.get(self.channel_id, {}).get("court", "Неизвестный суд")
        }
        
        embed = discord.Embed(
            title="⚖️ Судебное делопроизводство",
            description=(
                "**Статус иска:** 🟡 НА РАССМОТРЕНИИ\n\n"
                "Иск принят в работу сотрудниками суда.\n"
                "Ожидайте решения в этом канале."
            ),
            color=discord.Color.gold()
        )
        embed.set_footer(text="by Ilya Vetrov")
        
        await interaction.followup.send("✅ Иск переведён в статус «На рассмотрении»\n\nby Ilya Vetrov", ephemeral=True)
        await interaction.channel.send(embed=embed, view=StaffCloseButton(self.channel_id, self.author_id))
        
        try:
            await interaction.channel.purge(limit=1)
        except:
            pass

# ========== КНОПКА ЗАКРЫТИЯ ДЛЯ СОТРУДНИКОВ И АДМИНОВ ==========
class StaffCloseButton(View):
    def __init__(self, channel_id, author_id):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.author_id = author_id

    @discord.ui.button(label="🔒 Закрыть иск", style=discord.ButtonStyle.red, custom_id="staff_close_lawsuit")
    async def staff_close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        
        if not check_roles(interaction):
            await interaction.followup.send("❌ Только администраторы или сотрудники могут закрыть иск!\n\nby Ilya Vetrov", ephemeral=True)
            return
        
        await interaction.followup.send("🔒 Иск будет закрыт через 3 секунды...\n\nby Ilya Vetrov", ephemeral=True)
        
        os.makedirs("lawsuit_logs", exist_ok=True)
        log_file = f"lawsuit_logs/{interaction.channel.name}.txt"
        
        try:
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(f"Лог иска: {interaction.channel.name}\n")
                f.write(f"Дата закрытия: {datetime.utcnow()}\n")
                f.write(f"Закрыл сотрудник/админ: {interaction.user} ({interaction.user.id})\n")
                f.write("by Ilya Vetrov\n")
                f.write("="*50 + "\n")
                async for msg in interaction.channel.history(limit=500, oldest_first=True):
                    f.write(f"{msg.author} [{msg.created_at}]: {msg.content}\n")
        except Exception as e:
            logger.error(f"Ошибка сохранения лога: {e}")
        
        await asyncio.sleep(3)
        
        try:
            if self.channel_id in lawsuit_status:
                del lawsuit_status[self.channel_id]
            await interaction.channel.delete()
        except Exception as e:
            logger.error(f"Ошибка удаления канала: {e}")

# ========== СЛЭШ-КОМАНДЫ ==========

@bot.tree.command(name="setup", description="🔧 Настройка системы исков (админ)")
@app_commands.default_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    channel = interaction.guild.get_channel(PANEL_CHANNEL_ID)
    if not channel:
        await interaction.response.send_message(f"❌ Канал {PANEL_CHANNEL_ID} не найден!\n\nby Ilya Vetrov", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="⚖️ Судебная система Нижегородской области",
        description=(
            "**Подача искового заявления**\n\n"
            "Для подачи иска в один из судов нажмите кнопку ниже и выберите нужную инстанцию.\n"
            "После создания канала заполните форму, заменив прочерки своими данными.\n\n"
            "**Доступные суды:**\n"
            "• Областной Суд\n"
            "• Верховный Суд\n"
            "• Конституционный Суд\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "**Нажмите на кнопку, чтобы начать**"
        ),
        color=discord.Color.red()
    )
    embed.set_footer(text="by Ilya Vetrov")
    
    await channel.send(embed=embed, view=LawsuitButton(bot))
    await interaction.response.send_message(f"✅ Панель отправлена в канал {channel.mention}\n\nby Ilya Vetrov", ephemeral=True)

@bot.tree.command(name="force_close", description="⚠️ ПРИНУДИТЕЛЬНО закрыть любой иск (админ/сотрудник)")
@app_commands.describe(channel_id="ID канала с иском (например, 123456789012345678)")
async def force_close(interaction: discord.Interaction, channel_id: str = None):
    await interaction.response.defer(ephemeral=True)
    
    if not check_roles(interaction):
        await interaction.followup.send("❌ У вас нет прав для использования этой команды!\n\nby Ilya Vetrov", ephemeral=True)
        return
    
    if not channel_id:
        active_lawsuits = []
        for cid, data in lawsuit_status.items():
            channel = interaction.guild.get_channel(cid)
            if channel:
                active_lawsuits.append(f"📄 `{cid}` - {channel.name} (автор: <@{data.get('author_id')}>)")
        
        if not active_lawsuits:
            await interaction.followup.send("📭 Нет активных исков.\n\nby Ilya Vetrov", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="⚠️ Принудительное закрытие иска",
            description="Используйте: `/force_close ID_канала`\n\n**Активные иски:**\n" + "\n".join(active_lawsuits),
            color=discord.Color.orange()
        )
        embed.set_footer(text="by Ilya Vetrov")
        await interaction.followup.send(embed=embed, ephemeral=True)
        return
    
    try:
        channel_id_int = int(channel_id)
        channel = interaction.guild.get_channel(channel_id_int)
        
        if not channel:
            await interaction.followup.send(f"❌ Канал с ID `{channel_id}` не найден!\n\nby Ilya Vetrov", ephemeral=True)
            return
        
        if not channel.name.startswith("иск-"):
            await interaction.followup.send(f"❌ Канал `{channel.name}` не является каналом иска!\n\nby Ilya Vetrov", ephemeral=True)
            return
        
        await interaction.followup.send(f"⚠️ Принудительное закрытие канала `{channel.name}` через 3 секунды...\n\nby Ilya Vetrov", ephemeral=True)
        
        os.makedirs("lawsuit_logs", exist_ok=True)
        log_file = f"lawsuit_logs/{channel.name}.txt"
        
        try:
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(f"Лог иска: {channel.name}\n")
                f.write(f"Дата принудительного закрытия: {datetime.utcnow()}\n")
                f.write(f"Принудительно закрыл: {interaction.user} ({interaction.user.id})\n")
                f.write("by Ilya Vetrov\n")
                f.write("="*50 + "\n")
                async for msg in channel.history(limit=500, oldest_first=True):
                    f.write(f"{msg.author} [{msg.created_at}]: {msg.content}\n")
        except Exception as e:
            logger.error(f"Ошибка сохранения лога: {e}")
        
        await asyncio.sleep(3)
        
        if channel.id in lawsuit_status:
            del lawsuit_status[channel.id]
        await channel.delete()
        
    except ValueError:
        await interaction.followup.send(f"❌ Неверный формат ID. Введите числовой ID канала.\n\nby Ilya Vetrov", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Ошибка при закрытии: {str(e)[:100]}\n\nby Ilya Vetrov", ephemeral=True)

@bot.tree.command(name="lawsuit_log", description="📄 Получить лог закрытого иска")
@app_commands.describe(channel_name="Название канала иска (например, иск-иван-областной)")
async def lawsuit_log(interaction: discord.Interaction, channel_name: str = None):
    await interaction.response.defer(ephemeral=True)
    
    if not check_roles(interaction):
        await interaction.followup.send("❌ Нет прав!\n\nby Ilya Vetrov", ephemeral=True)
        return
    
    if not channel_name:
        await interaction.followup.send("❌ Укажите название канала\n\nby Ilya Vetrov", ephemeral=True)
        return
    
    log_path = f"lawsuit_logs/{channel_name}.txt"
    if not os.path.exists(log_path):
        await interaction.followup.send(f"❌ Лог для `{channel_name}` не найден.\n\nby Ilya Vetrov", ephemeral=True)
        return
    
    await interaction.followup.send(file=discord.File(log_path))

@bot.tree.command(name="closed_list", description="📋 Список всех закрытых исков")
async def closed_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    if not check_roles(interaction):
        await interaction.followup.send("❌ Нет прав!\n\nby Ilya Vetrov", ephemeral=True)
        return
    
    os.makedirs("lawsuit_logs", exist_ok=True)
    files = [f.replace(".txt", "") for f in os.listdir("lawsuit_logs") if f.endswith(".txt")]
    
    if not files:
        await interaction.followup.send("📭 Нет закрытых исков.\n\nby Ilya Vetrov", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="📋 Список закрытых исков",
        description="\n".join([f"📄 `{f}`" for f in files]),
        color=discord.Color.blue()
    )
    embed.set_footer(text="by Ilya Vetrov")
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="active_list", description="📋 Список активных (открытых) исков")
async def active_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    if not check_roles(interaction):
        await interaction.followup.send("❌ Нет прав!\n\nby Ilya Vetrov", ephemeral=True)
        return
    
    active_lawsuits = []
    for cid, data in lawsuit_status.items():
        channel = interaction.guild.get_channel(cid)
        if channel:
            status_text = "🟢 ОЖИДАЕТ" if data.get("status") == "waiting" else "🟡 НА РАССМОТРЕНИИ"
            court = data.get("court", "Неизвестный суд")
            active_lawsuits.append(f"📄 `{channel.name}` - {status_text} ({court}) (автор: <@{data.get('author_id')}>)")
    
    if not active_lawsuits:
        await interaction.followup.send("📭 Нет активных исков.\n\nby Ilya Vetrov", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="📋 Список активных исков",
        description="\n".join(active_lawsuits),
        color=discord.Color.green()
    )
    embed.set_footer(text="by Ilya Vetrov")
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="info", description="ℹ️ Информация о боте")
async def info(interaction: discord.Interaction):
    embed = discord.Embed(
        title="ℹ️ О боте",
        description=(
            "**Система подачи исковых заявлений в суды Нижегородской области**\n\n"
            "👨‍💻 **Разработчик:** Ilya Vetrov\n"
            "🛡️ **Версия:** 3.0 (Судебная система)\n\n"
            "**Команды:**\n"
            "• `/setup` - Настройка панели (админ)\n"
            "• `/force_close` - Принудительно закрыть иск\n"
            "• `/active_list` - Список активных исков\n"
            "• `/closed_list` - Список закрытых исков\n"
            "• `/lawsuit_log` - Лог закрытого иска\n"
            "• `/check_roles` - Проверка ролей\n"
            "• `/info` - Эта информация\n\n"
            "**Кто может закрыть иск:**\n"
            "• Автор иска (до рассмотрения)\n"
            "• Администратор сервера\n"
            "• Сотрудник с ролью"
        ),
        color=discord.Color.blue()
    )
    embed.set_footer(text="by Ilya Vetrov")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="check_roles", description="👥 Проверить какие роли видят иски")
async def check_roles_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    if not check_roles(interaction):
        await interaction.followup.send("❌ Нет прав!\n\nby Ilya Vetrov", ephemeral=True)
        return
    
    roles_list = []
    for role_id in ROLE_IDS:
        role = interaction.guild.get_role(role_id)
        if role:
            roles_list.append(f"✅ {role.name} (`{role_id}`)")
        else:
            roles_list.append(f"❌ Роль не найдена (`{role_id}`)")
    
    embed = discord.Embed(
        title="👥 Роли с доступом к искам",
        description="\n".join(roles_list) + "\n\n✅ Администраторы сервера также имеют полный доступ",
        color=discord.Color.blue()
    )
    embed.set_footer(text="by Ilya Vetrov")
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.event
async def on_ready():
    logger.info(f"✅ Бот {bot.user} запущен!")
    await bot.change_presence(activity=discord.Game(name="Судебная система | by Ilya Vetrov"))
    print(f"✅ Бот {bot.user} готов!")
    print(f"👨‍💻 by Ilya Vetrov")
    print(f"🔧 Доступные команды: /setup, /force_close, /active_list, /closed_list, /lawsuit_log, /check_roles, /info")

if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception as e:
        logger.error(f"Ошибка запуска: {e}")
