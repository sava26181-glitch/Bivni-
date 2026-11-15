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
from curl_cffi.requests import AsyncSession

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
DEFAULT_CONFIG = {
    "golden_key": "",
    "epic_accounts": [],
    "lots": []
}
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
    waiting_for_lot_title = State()
    waiting_for_lot_desc = State()
    waiting_for_lot_price = State()
    waiting_for_lot_photo = State()
    waiting_for_epic_email = State()
    waiting_for_epic_pass = State()
    waiting_for_withdraw_amount = State()

main_router = Router()

# --- Функции FunPay ---
async def funpay_toggle_status(status: str) -> bool:
    if not CONFIG["golden_key"]: return False
    
    headers = {"x-requested-with": "XMLHttpRequest"}
    cookies = {"golden_key": CONFIG["golden_key"]}
    
    async with AsyncSession(impersonate="chrome120") as session:
        resp = await session.get("https://funpay.com/", cookies=cookies, headers=headers)
        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', resp.text)
        csrf = csrf_match.group(1) if csrf_match else ""
        
        payload = {
            "onlines": "enable" if status == "online" else "disable",
            "csrf_token": csrf
        }
        res = await session.post("https://funpay.com/runner/", data=payload, cookies=cookies, headers=headers)
        return res.status_code == 200

async def funpay_withdraw(amount: float, wallet: str, currency: str = "rub") -> bool:
    if not CONFIG["golden_key"]: return False
    
    headers = {"x-requested-with": "XMLHttpRequest"}
    cookies = {"golden_key": CONFIG["golden_key"]}
    
    async with AsyncSession(impersonate="chrome120") as session:
        resp = await session.get("https://funpay.com/account/balance", cookies=cookies, headers=headers)
        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', resp.text)
        csrf = csrf_match.group(1) if csrf_match else ""
        
        payload = {
            "csrf_token": csrf,
            "preview": "",
            "currency_id": currency,
            "ext_currency_id": "card_rub",
            "wallet": wallet,
            "amount_int": amount
        }
        res = await session.post("https://funpay.com/withdraw/withdraw", data=payload, cookies=cookies, headers=headers)
        return res.status_code == 200

async def funpay_create_lot(title: str, desc: str, price: float, category_id: int = 2418) -> bool:
    if not CONFIG["golden_key"]: return False
    
    headers = {"x-requested-with": "XMLHttpRequest"}
    cookies = {"golden_key": CONFIG["golden_key"]}
    
    async with AsyncSession(impersonate="chrome120") as session:
        resp = await session.get("https://funpay.com/lots/new", cookies=cookies, headers=headers)
        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', resp.text)
        csrf = csrf_match.group(1) if csrf_match else ""
        
        # Внимание: структура payload зависит от реальной формы FunPay!
        payload = {
            "csrf_token": csrf,
            "category_id": category_id,
            "subcategory_id": "", 
            "title": title,
            "description": desc,
            "price": price,
            "amount": 1,
            "active": "on"
        }
        res = await session.post("https://funpay.com/lots/save", data=payload, cookies=cookies, headers=headers)
        return res.status_code == 200

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
        logging.error(f"Ошибка отправки фото: {e}")
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

@main_router.message(Command("toggle_status"))
async def cmd_toggle_status(msg: types.Message):
    await msg.answer("🔄 Меняю статус на ОФФЛАЙН...")
    success = await funpay_toggle_status("offline")
    if success:
        await msg.answer("✅ Аккаунт переведен в ОФФЛАЙН.")
    else:
        await msg.answer("❌ Ошибка смены статуса. Проверь golden_key.")

@main_router.message(Command("withdraw"))
async def cmd_withdraw(msg: types.Message, state: FSMContext):
    await msg.answer("Введи сумму для вывода (в рублях):")
    await state.set_state(BotStates.waiting_for_withdraw_amount)

@main_router.message(BotStates.waiting_for_withdraw_amount)
async def process_withdraw(msg: types.Message, state: FSMContext):
    try:
        amount = float(msg.text)
        await msg.answer("🔄 Отправляю запрос на вывод...")
        success = await funpay_withdraw(amount, "1111 2222 3333 4444") # Замени на карту
        if success:
            await msg.answer(f"✅ Запрос на вывод {amount} ₽ отправлен.")
        else:
            await msg.answer("❌ Ошибка вывода. Проверь golden_key и карту.")
    except:
        await msg.answer("❌ Введи число.")
    await state.clear()

# --- Создание лота ---
@main_router.message(Command("create_lot"))
async def cmd_create_lot(msg: types.Message, state: FSMContext):
    await msg.answer("Введи название лота:")
    await state.set_state(BotStates.waiting_for_lot_title)

@main_router.message(BotStates.waiting_for_lot_title)
async def process_lot_title(msg: types.Message, state: FSMContext):
    await state.update_data(title=msg.text)
    await msg.answer("Введи описание лота (с указанием, что почта не полная):")
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

    await msg.answer("🔄 Отправляю лот на FunPay...")
    
    success = await funpay_create_lot(data["title"], data["desc"], data["price"])
    
    if success:
        CONFIG["lots"].append({
            "title": data["title"],
            "desc": data["desc"],
            "price": data["price"],
            "photo": photo_id
        })
        save_config()
        await msg.answer(f"✅ Лот '{data['title']}' создан на FunPay и добавлен в базу.")
    else:
        await msg.answer("⚠️ Не удалось создать лот на FunPay. Возможно, неверный CSRF или изменился API.")
        
    await state.clear()

@main_router.message(Command("list_lots"))
async def cmd_list_lots(msg: types.Message):
    if not CONFIG["lots"]:
        await msg.answer("База лотов пуста.")
        return
    
    text = "📦 <b>Твои лоты:</b>\n\n"
    for i, lot in enumerate(CONFIG["lots"], 1):
        text += f"{i}. <b>{lot['title']}</b>\nЦена: {lot['price']} ₽\nОписание: {lot['desc']}\n\n"
    await msg.answer(text, parse_mode="HTML")

# --- Добавление аккаунта Epic ---
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
