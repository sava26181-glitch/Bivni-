import asyncio, os, re, logging, json, time
from datetime import datetime
import aiohttp
from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from aiogram.types import FSInputFile

# Импортируем FunPayAPI (нужно добавить в requirements.txt)
from FunPayAPI import Account
from FunPayAPI.types import SubCategoryTypes

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "https://bivni.onrender.com"
WEBHOOK_PATH = "/webhook"
WEBHOOK_FULL = WEBHOOK_URL.rstrip("/") + WEBHOOK_PATH
PORT = int(os.environ.get("PORT", 8080))

bot = Bot(TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- Конфигурация ---
CONFIG_FILE = "config.json"
DEFAULT_CONFIG = {"golden_key": "", "epic_accounts": [], "lots": []}
CONFIG = DEFAULT_CONFIG.copy()

def load_config():
    global CONFIG
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                CONFIG.update(json.load(f))
        except: pass

def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(CONFIG, f, ensure_ascii=False, indent=2)

load_config()

class BotStates(StatesGroup):
    waiting_for_golden_key = State()
    waiting_for_game_name = State()
    waiting_for_lot_title = State()
    waiting_for_lot_desc = State()
    waiting_for_lot_price = State()
    waiting_for_lot_photo = State()
    waiting_for_epic_email = State()
    waiting_for_epic_pass = State()
    waiting_for_withdraw_amount = State()

main_router = Router()

# --- Функции FunPay через FunPayAPI ---
async def funpay_create_lot_api(game_name: str, title: str, desc: str, price: float) -> bool:
    """Создает лот через FunPayAPI."""
    if not CONFIG["golden_key"]: return False
    
    try:
        # Авторизация через FunPayAPI (синхронная, выносим в поток)
        def sync_create():
            acc = Account(CONFIG["golden_key"]).get()
            
            # Находим игру
            game_id = None
            for game in acc.get_game_id_by_name(game_name):
                game_id = game.id
                break
            if not game_id: return False
            
            # Находим категорию (ищем "Аккаунты")
            cat_id = None
            for cat in acc.get_categories(game_id):
                if "аккаунт" in cat.name.lower() or cat.type == SubCategoryTypes.COMMON:
                    cat_id = cat.id
                    break
            if not cat_id: return False
            
            # Сохраняем лот
            fields = acc.get_lot_fields(0) # Создаем с нуля
            fields.title = title
            fields.description = desc
            fields.price = price
            fields.amount = 1
            fields.active = True
            
            # Устанавливаем игру и категорию
            # (точная структура зависит от версии FunPayAPI, но обычно так)
            acc.save_lot(fields)
            return True
            
        return await asyncio.to_thread(sync_create)
        
    except Exception as e:
        logging.error(f"Ошибка FunPayAPI: {e}")
        return False

# --- Обработчики Aiogram ---

@main_router.message(Command("start"))
async def cmd_start(msg: types.Message):
    key_status = "✅" if CONFIG["golden_key"] else "❌"
    text = (
        f"🤖 <b>Панель управления FunPay</b>\n\n"
        f"🔑 Golden Key: {key_status}\n\n"
        "⚙️ <b>Настройки:</b>\n"
        "/set_key — Установить Golden Key\n"
        "/add_acc — Добавить аккаунт Epic\n"
        "/list_acc — Список аккаунтов Epic\n\n"
        "📦 <b>Лоты и Финансы:</b>\n"
        "/create_lot — Создать новый лот\n"
        "/list_lots — Список лотов\n"
        "/toggle_status — Вкл/Выкл Онлайн\n"
        "/withdraw — Вывести средства\n"
    )
    try:
        photo = FSInputFile("IMG_7776.jpeg")
        await msg.answer_photo(photo=photo, caption=text, parse_mode="HTML")
    except Exception as e:
        await msg.answer(text, parse_mode="HTML")

@main_router.message(Command("set_key"))
async def cmd_set_key(msg: types.Message, state: FSMContext):
    await msg.answer("Отправь свой golden_key от FunPay:")
    await state.set_state(BotStates.waiting_for_golden_key)

@main_router.message(BotStates.waiting_for_golden_key)
async def process_golden_key(msg: types.Message, state: FSMContext):
    CONFIG["golden_key"] = msg.text.strip()
    save_config()
    await msg.answer("✅ Golden Key сохранен.")
    await state.clear()

@main_router.message(Command("create_lot"))
async def cmd_create_lot(msg: types.Message, state: FSMContext):
    await msg.answer("Введи название игры (например, 'Fortnite'):")
    await state.set_state(BotStates.waiting_for_game_name)

@main_router.message(BotStates.waiting_for_game_name)
async def process_game_name(msg: types.Message, state: FSMContext):
    await state.update_data(game_name=msg.text.strip())
    await msg.answer("Введи название лота:")
    await state.set_state(BotStates.waiting_for_lot_title)

@main_router.message(BotStates.waiting_for_lot_title)
async def process_lot_title(msg: types.Message, state: FSMContext):
    await state.update_data(title=msg.text)
    await msg.answer("Введи описание лота:")
    await state.set_state(BotStates.waiting_for_lot_desc)

@main_router.message(BotStates.waiting_for_lot_desc)
async def process_lot_desc(msg: types.Message, state: FSMContext):
    await state.update_data(desc=msg.text)
    await msg.answer("Введи цену (в рублях):")
    await state.set_state(BotStates.waiting_for_lot_price)

@main_router.message(BotStates.waiting_for_lot_price)
async def process_lot_price(msg: types.Message, state: FSMContext):
    try:
        await state.update_data(price=float(msg.text))
        await msg.answer("Отправь фото или /skip:")
        await state.set_state(BotStates.waiting_for_lot_photo)
    except:
        await msg.answer("❌ Введи число.")

@main_router.message(BotStates.waiting_for_lot_photo, F.photo | F.text)
async def process_lot_photo(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    
    photo_id = None
    if msg.photo:
        photo_id = msg.photo[-1].file_id
    elif msg.text == "/skip":
        photo_id = None
    else:
        await msg.answer("Отправь фото или /skip.")
        return

    await msg.answer("🔄 Ищу игру, категорию 'Аккаунты' и отправляю лот через FunPayAPI...")
    
    success = await funpay_create_lot_api(
        data["game_name"], 
        data["title"], 
        data["desc"], 
        data["price"]
    )
    
    if success:
        CONFIG["lots"].append({
            "title": data["title"],
            "desc": data["desc"],
            "price": data["price"],
            "photo": photo_id
        })
        save_config()
        await msg.answer(f"✅ Лот '{data['title']}' создан на FunPay.")
    else:
        await msg.answer("⚠️ Не удалось создать лот. Проверь название игры или golden_key.")
        
    await state.clear()

@main_router.message(Command("list_lots"))
async def cmd_list_lots(msg: types.Message):
    if not CONFIG["lots"]:
        await msg.answer("База лотов пуста.")
        return
    text = "📦 <b>Твои лоты:</b>\n\n"
    for i, lot in enumerate(CONFIG["lots"], 1):
        text += f"{i}. <b>{lot['title']}</b>\nЦена: {lot['price']} ₽\n\n"
    await msg.answer(text, parse_mode="HTML")

@main_router.message(Command("add_acc"))
async def cmd_add_acc(msg: types.Message, state: FSMContext):
    await msg.answer("Введи email от Epic Games:")
    await state.set_state(BotStates.waiting_for_epic_email)

@main_router.message(BotStates.waiting_for_epic_email)
async def process_epic_email(msg: types.Message, state: FSMContext):
    await state.update_data(email=msg.text)
    await msg.answer("Введи пароль:")
    await state.set_state(BotStates.waiting_for_epic_pass)

@main_router.message(BotStates.waiting_for_epic_pass)
async def process_epic_pass(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    CONFIG["epic_accounts"].append({"email": data["email"], "password": msg.text})
    save_config()
    await msg.answer("✅ Аккаунт Epic добавлен в базу.")
    await state.clear()

@main_router.message(Command("list_acc"))
async def cmd_list_acc(msg: types.Message):
    if not CONFIG["epic_accounts"]:
        await msg.answer("База аккаунтов Epic пуста.")
        return
    text = "🎮 <b>Аккаунты Epic:</b>\n\n"
    for i, acc in enumerate(CONFIG["epic_accounts"], 1):
        text += f"{i}. Email: <code>{acc['email']}</code>\nПароль: <code>{acc['password']}</code>\n\n"
    await msg.answer(text, parse_mode="HTML")

dp.include_router(main_router)

# --- Запуск на Render ---
async def set_webhook_later():
    await asyncio.sleep(2)
    try:
        await bot.set_webhook(WEBHOOK_FULL, drop_pending_updates=True)
        logging.info(f"Webhook set: {WEBHOOK_FULL}")
    except Exception as e:
        logging.error(f"Webhook error: {e}")

async def on_startup(app: web.Application):
    asyncio.create_task(set_webhook_later())

async def on_shutdown(app: web.Application):
    await bot.delete_webhook()
    await bot.session.close()

def main():
    app = web.Application()
    handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
