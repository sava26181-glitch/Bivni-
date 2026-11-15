import asyncio, os, re, logging, json, time
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from curl_cffi.requests import AsyncSession

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "https://fakefarm.onrender.com"
WEBHOOK_URL = WEBHOOK_URL.rstrip("/")
WEBHOOK_PATH = "/webhook"
WEBHOOK_FULL = WEBHOOK_URL + WEBHOOK_PATH
PORT = int(os.environ.get("PORT", 8080))

bot = Bot(TOKEN)
dp = Dispatcher(storage=MemoryStorage())

FUNPAY_GOLDEN_KEY = ""
TIMERS = {}
STATS = {"total_scammed": 0, "total_money": 0.0}

class SetupState(StatesGroup):
    waiting_for_login = State()
    waiting_for_password = State()

main_router = Router()

@main_router.message(Command("start"))
async def cmd_start(msg: types.Message):
    await msg.answer(
        "🤖 Бот-таймер FunPay запущен.\n"
        "/login — Автоматически получить golden_key\n"
        "/set_key [KEY] — Установить ключ вручную\n"
        "/stats — Статистика\n"
        "/check_orders — Запустить мониторинг\n"
    )

@main_router.message(Command("set_key"))
async def cmd_set_key(msg: types.Message):
    global FUNPAY_GOLDEN_KEY
    args = msg.text.split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("Формат: /set_key <ваш_golden_key>")
        return
    FUNPAY_GOLDEN_KEY = args[1].strip()
    await msg.answer(f"✅ Ключ установлен вручную (начинается на {FUNPAY_GOLDEN_KEY[:5]}...)")

@main_router.message(Command("login"))
async def cmd_login(msg: types.Message, state: FSMContext):
    await msg.answer("Отправь свой логин от FunPay (почту или ник):")
    await state.set_state(SetupState.waiting_for_login)

@main_router.message(SetupState.waiting_for_login)
async def process_login(msg: types.Message, state: FSMContext):
    await state.update_data(login=msg.text.strip())
    await msg.answer("Теперь отправь пароль:")
    await state.set_state(SetupState.waiting_for_password)

@main_router.message(SetupState.waiting_for_password)
async def process_password(msg: types.Message, state: FSMContext):
    global FUNPAY_GOLDEN_KEY
    data = await state.get_data()
    login = data["login"]
    password = msg.text.strip()
    
    await msg.answer("🔄 Авторизуюсь на FunPay...")
    
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": "https://funpay.com",
        "Referer": "https://funpay.com/",
    }
    
    try:
        async with AsyncSession(impersonate="chrome120") as session:
            # 1. Получаем CSRF
            resp = await session.get("https://funpay.com/account/login", headers=headers, timeout=15)
            csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', resp.text)
            csrf = csrf_match.group(1) if csrf_match else ""
            
            if not csrf:
                await msg.answer("❌ Не удалось получить CSRF-токен. Возможно, Cloudflare блокирует.")
                await state.clear()
                return

            # 2. Логин (форма, не JSON)
            payload = {
                "login": login,
                "password": password,
                "csrf_token": csrf
            }
            
            login_resp = await session.post("https://funpay.com/account/login", data=payload, headers=headers, timeout=15)
            
            # 3. Вытягиваем golden_key
            golden_key = None
            for cookie in session.cookies:
                if cookie.name == "golden_key":
                    golden_key = cookie.value
                    break
            
            if golden_key:
                FUNPAY_GOLDEN_KEY = golden_key
                await msg.answer(f"✅ Успешно! golden_key сохранен.")
            else:
                await msg.answer("❌ Не удалось получить golden_key. Проверь логин/пароль или капчу.")
                
    except Exception as e:
        await msg.answer(f"❌ Ошибка: {e}")
        
    await state.clear()

async def fetch_funpay_orders():
    if not FUNPAY_GOLDEN_KEY:
        return []
    await asyncio.sleep(1)
    # В реальности здесь парсинг https://funpay.com/orders/trade с куки golden_key
    return [{"id": f"ORDER_{int(time.time())}", "account_name": "EpicAcc_123", "price": 1500}]

async def monitor_orders():
    while True:
        try:
            orders = await fetch_funpay_orders()
            for order in orders:
                order_id = order["id"]
                if order_id not in TIMERS:
                    end_time = time.time() + 172800
                    TIMERS[order_id] = {"end_time": end_time, "account_name": order["account_name"], "price": order["price"], "notified": False}
            
            now = time.time()
            for order_id, data in list(TIMERS.items()):
                if not data["notified"] and now >= data["end_time"]:
                    text = f"⏰ <b>Время вышло!</b>\nЛот: {data['account_name']}\nСумма: {data['price']} ₽\nЗаказ {order_id} можно отжимать."
                    try:
                        ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))
                        if ADMIN_ID:
                            await bot.send_message(ADMIN_ID, text, parse_mode="HTML")
                    except:
                        pass
                    data["notified"] = True
                    STATS["total_scammed"] += 1
                    STATS["total_money"] += data["price"]
                    del TIMERS[order_id]
        except Exception as e:
            logging.error(f"Ошибка: {e}")
        await asyncio.sleep(60)

@main_router.message(Command("check_orders"))
async def cmd_check_orders(msg: types.Message):
    if not FUNPAY_GOLDEN_KEY:
        await msg.answer("Сначала авторизуйся: /login")
        return
    asyncio.create_task(monitor_orders())
    await msg.answer("🔄 Мониторинг запущен в фоне.")

@main_router.message(Command("stats"))
async def cmd_stats(msg: types.Message):
    await msg.answer(
        f"📊 <b>Статистика:</b>\n"
        f"💀 Убито аккаунтов: {STATS['total_scammed']}\n"
        f"💰 Наебали на: {STATS['total_money']} ₽\n"
        f"⏳ Таймеров: {len(TIMERS)}",
        parse_mode="HTML"
    )

dp.include_router(main_router)

async def set_webhook_later():
    await asyncio.sleep(2)
    try:
        await bot.set_webhook(WEBHOOK_FULL, drop_pending_updates=True)
    except Exception as e:
        print("Webhook error:", e)

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
