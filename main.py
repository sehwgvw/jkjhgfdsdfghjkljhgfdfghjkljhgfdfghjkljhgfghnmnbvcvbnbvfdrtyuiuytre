from __future__ import annotations
import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

class Settings:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    API_ID = int(os.getenv("API_ID", 0))  # Telethon требует API_ID как число
    API_HASH = os.getenv("API_HASH")
    CRYPTO_BOT_TOKEN = os.getenv("CRYPTO_BOT_TOKEN")
    
    # Превращаем строку с ID через запятую в список чисел
    admin_ids_str = os.getenv("TRUSTED_ADMIN_IDS", "")
    TRUSTED_ADMIN_IDS = [int(i) for i in admin_ids_str.split(",") if i.strip()]
    
    MAX_PARALLEL_REPORTS = int(os.getenv("MAX_PARALLEL_REPORTS", 5))
    QUEUE_WORKERS = int(os.getenv("QUEUE_WORKERS", 3))
    DRY_LOG_TO_ADMIN = os.getenv("DRY_LOG_TO_ADMIN") == "1"

settings = Settings()


import hashlib
import logging
import time

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession

from bot_session import IPv4AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from compliance_engine import ComplianceEngine
from config import BASE_DIR, LOGS_DIR, PROXIES_DIR, SESSIONS_DIR, load_settings
from crypto_payments import CryptoPayClient
from proxy_handler import ProxyPool
from support_form import (
    build_web_letter,
    random_email,
    random_full_name_ru,
    random_phone,
    submit_telegram_support_form,
)
from user_store import UserStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.FileHandler(LOGS_DIR / "bot.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("controller")


PLAN_PRICES = {"week": 2.0, "month": 5.0, "year": 15.0, "lifetime": 50.0}
PLAN_LABELS = {"week": "Неделя", "month": "Месяц", "year": "Год", "lifetime": "Навсегда"}
REPORT_REASONS = ["spam", "violence", "pornography", "childabuse", "copyright", "other"]
REASON_LABELS = {
    "spam": "Спам",
    "violence": "Насилие/угрозы",
    "pornography": "Порнография",
    "childabuse": "Защита детей",
    "copyright": "Авторские права",
    "other": "Другое",
}
SUBREASONS = {
    "spam": ["Реклама", "Бот-спам", "Массовые упоминания"],
    "violence": ["Угрозы", "Призывы к насилию", "Шок-контент"],
    "pornography": ["Откровенные материалы", "18+ без маркировки", "Нежелательный контент"],
    "childabuse": ["Эксплуатация детей", "Опасный контент для детей", "Подозрительные материалы"],
    "copyright": ["Пиратский контент", "Нарушение лицензии", "Кража авторства"],
    "other": ["Мошенничество", "Фишинг", "Иное нарушение"],
}


class MenuState(StatesGroup):
    waiting_moderation_route = State()
    waiting_web_proxy = State()
    waiting_moderation_target = State()
    waiting_moderation_reason = State()
    waiting_moderation_subreason = State()
    waiting_moderation_comment = State()
    waiting_moderation_count = State()
    waiting_web_entity_kind = State()
    waiting_web_user_id = State()
    waiting_web_channel_link = State()
    waiting_web_message_link = State()
    waiting_web_reason = State()
    waiting_proxy_input = State()
    waiting_session_file = State()
    waiting_promo_code = State()
    waiting_admin_promo = State()


def is_admin(user_id: int | None, trusted_ids: set[int]) -> bool:
    return bool(user_id) and (not trusted_ids or user_id in trusted_ids)


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Запустить модерацию", callback_data="menu:moderation")],
            [InlineKeyboardButton(text="🧩 Добавить мой прокси", callback_data="menu:add_proxy")],
            [InlineKeyboardButton(text="📁 Добавить мою сессию", callback_data="menu:add_session")],
            [InlineKeyboardButton(text="👤 Профиль", callback_data="menu:profile")],
            [InlineKeyboardButton(text="📊 Получить статистику", callback_data="menu:stats")],
            [InlineKeyboardButton(text="🛟 Тех. поддержка", url="https://t.me/Nyawka_CuteUwU")],
        ]
    )


def kb_buy() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Неделя - 2 USDT", callback_data="buy:week")],
            [InlineKeyboardButton(text="Месяц - 5 USDT", callback_data="buy:month")],
            [InlineKeyboardButton(text="Год - 15 USDT", callback_data="buy:year")],
            [InlineKeyboardButton(text="Навсегда - 50 USDT", callback_data="buy:lifetime")],
            [InlineKeyboardButton(text="Проверить оплату", callback_data="buy:check")],
            [InlineKeyboardButton(text="Назад", callback_data="menu:main")],
        ]
    )


def kb_profile(is_admin_user: bool) -> InlineKeyboardMarkup:
    rows = []
    if not is_admin_user:
        rows.extend(
            [
                [InlineKeyboardButton(text="💳 Купить подписку", callback_data="menu:buy")],
                [InlineKeyboardButton(text="🎁 Активировать промокод", callback_data="profile:promo")],
                [InlineKeyboardButton(text="⭐ Обменять 10 баллов = 1 неделя", callback_data="profile:redeem")],
            ]
        )
    else:
        rows.append([InlineKeyboardButton(text="🛠 Админ панель", callback_data="admin:panel")])
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_reasons() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text=REASON_LABELS[r], callback_data=f"reason:{r}")] for r in REPORT_REASONS]
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_subreasons(reason: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=item, callback_data=f"subreason:{idx}")] for idx, item in enumerate(SUBREASONS[reason])]
    rows.append([InlineKeyboardButton(text="Назад", callback_data="menu:main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="menu:main")]])


def kb_moderation_route() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📡 Репорты через API (сессии)", callback_data="modroute:api")],
            [InlineKeyboardButton(text="🌐 Форма telegram.org/support", callback_data="modroute:web")],
            [InlineKeyboardButton(text="Назад", callback_data="menu:main")],
        ]
    )


def kb_web_form_proxy_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Да, через прокси из пула", callback_data="webproxy:1")],
            [InlineKeyboardButton(text="Нет, с IP сервера", callback_data="webproxy:0")],
            [InlineKeyboardButton(text="Назад", callback_data="menu:main")],
        ]
    )


def kb_web_entity_kind() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пользователь", callback_data="webent:user")],
            [InlineKeyboardButton(text="Канал", callback_data="webent:channel")],
            [InlineKeyboardButton(text="Назад", callback_data="menu:main")],
        ]
    )


def kb_sorry() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛟 Тех. поддержка", url="https://t.me/Nyawka_CuteUwU")],
            [InlineKeyboardButton(text="💳 Купить подписку", callback_data="menu:buy")],
            [InlineKeyboardButton(text="🎁 Активировать промокод", callback_data="profile:promo")],
        ]
    )


async def send_banner(message: Message, name: str, text: str, kb: InlineKeyboardMarkup) -> None:
    banner_path = BASE_DIR / "banners" / f"{name}.png"
    async def _safe_answer_text() -> None:
        last_exc: Exception | None = None
        for delay in (0, 1, 2):
            if delay:
                await asyncio.sleep(delay)
            try:
                await message.answer(text, reply_markup=kb)
                return
            except Exception as exc:
                last_exc = exc
        if last_exc:
            logger.warning("Failed to send text response: %s", type(last_exc).__name__)

    if banner_path.exists():
        last_exc: Exception | None = None
        for delay in (0, 1, 2):
            if delay:
                await asyncio.sleep(delay)
            try:
                await message.answer_photo(FSInputFile(str(banner_path)), caption=text, reply_markup=kb)
                return
            except Exception as exc:
                last_exc = exc
        logger.warning("Banner send failed (%s), fallback to text", type(last_exc).__name__ if last_exc else "unknown")
        await _safe_answer_text()
        return

    await _safe_answer_text()


def parse_process_args(text: str) -> tuple[str, str, int]:
    parts = text.split()
    if len(parts) < 4:
        raise ValueError("Format: target reason count")
    target, reason, count = parts[1], parts[2].lower(), int(parts[3])
    if reason not in REPORT_REASONS:
        raise ValueError(f"reason must be one of: {', '.join(REPORT_REASONS)}")
    return target, reason, count


def user_has_moderation_access(user_id: int, trusted_ids: set[int], profile) -> bool:
    if user_id in trusted_ids:
        return True
    return profile.is_active


async def process_proxy_text(
    raw_text: str,
    user_id: int,
    proxy_pool: ProxyPool,
    store: UserStore,
) -> dict[str, int]:
    parsed = list(proxy_pool._parse_noisy_text(raw_text))
    if not parsed:
        return {"parsed": 0, "valid": 0, "added": 0, "awarded": 0, "total": 0}
    serial = [f"{p.scheme}://{p.host}:{p.port}:{p.username or ''}:{p.password or ''}" for p in parsed]
    added, awarded, total = store.register_proxies(user_id, serial)
    if added:
        with (PROXIES_DIR / f"user_{user_id}.txt").open("a", encoding="utf-8") as f:
            for row in parsed:
                line = f"{row.scheme}://"
                if row.username and row.password:
                    line += f"{row.username}:{row.password}@"
                line += f"{row.host}:{row.port}\n"
                f.write(line)
    proxy_pool.reload()
    return {"parsed": len(parsed), "valid": len(parsed), "added": added, "awarded": awarded, "total": total}


async def main() -> None:
    settings = load_settings()
    session_cls = IPv4AiohttpSession if settings.bot_api_force_ipv4 else AiohttpSession
    aio_session = session_cls(proxy=None, limit=100, timeout=settings.bot_api_timeout)
    bot = Bot(settings.bot_token, session=aio_session)
    logger.info(
        "Bot API: прямое подключение, таймаут %.0fs, IPv4-only=%s",
        settings.bot_api_timeout,
        settings.bot_api_force_ipv4,
    )
    dp = Dispatcher()
    proxy_pool = ProxyPool(PROXIES_DIR)
    engine: ComplianceEngine | None = None
    store = UserStore(LOGS_DIR / "users.db")
    crypto = CryptoPayClient(settings.crypto_bot_token)
    if settings.telethon_enabled:
        engine = ComplianceEngine(settings=settings)
        init_stats = await engine.initialize()
        logger.info("Engine initialized: %s", init_stats)
    else:
        logger.warning("Telethon disabled: API_ID/API_HASH missing. Bot started in limited mode.")

    async def try_activate_invoice(row) -> bool:
        try:
            invoice = await crypto.get_invoice(row["invoice_id"])
            if invoice.get("status") != "paid":
                return False
            store.mark_invoice_paid(str(row["invoice_id"]))
            store.add_subscription(int(row["user_id"]), row["plan"])
            await bot.send_message(int(row["user_id"]), f"Оплата подтверждена. Тариф активирован: {row['plan']}")
            return True
        except Exception as exc:
            logger.warning("Invoice check failed for %s: %s", row["invoice_id"], type(exc).__name__)
            return False

    stop_bg = asyncio.Event()

    async def payments_watcher() -> None:
        while not stop_bg.is_set():
            for row in store.get_unpaid_invoices():
                await try_activate_invoice(row)
            try:
                await asyncio.wait_for(stop_bg.wait(), timeout=20)
            except asyncio.TimeoutError:
                continue

    async def show_sorry(message: Message) -> None:
        await send_banner(
            message,
            "sorry",
            (
                "😿 Извините, доступ в бота возможен только при активной подписке.\n"
                "Вы можете купить подписку, активировать промокод или написать в тех. поддержку."
            ),
            kb_sorry(),
        )

    def is_engine_ready() -> bool:
        return engine is not None and settings.telethon_enabled

    async def _handle_start(message: Message, state: FSMContext) -> None:
        user_id = message.from_user.id if message.from_user else None
        if user_id is None:
            return
        profile = store.get_or_create_user(user_id)
        if not user_has_moderation_access(user_id, settings.trusted_admin_ids, profile):
            await state.clear()
            await show_sorry(message)
            return
        await state.clear()
        await send_banner(
            message,
            "main",
            "🌸 Добро пожаловать в «Сносер Няшка»!\nВыберите нужный раздел в меню ниже 💫",
            kb_main(),
        )

    @dp.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext) -> None:
        await _handle_start(message, state)

    @dp.message(F.text.regexp(r"^/start(?:@[\w_]+)?(?:\s.*)?$"))
    async def cmd_start_text_fallback(message: Message, state: FSMContext) -> None:
        await _handle_start(message, state)

    @dp.callback_query(F.data == "menu:main")
    async def cb_main(callback: CallbackQuery, state: FSMContext) -> None:
        profile = store.get_or_create_user(callback.from_user.id)
        if not user_has_moderation_access(callback.from_user.id, settings.trusted_admin_ids, profile):
            await state.clear()
            await show_sorry(callback.message)
            try:
                await callback.answer()
            except Exception:
                pass
            return
        await state.clear()
        await send_banner(callback.message, "main", "🌸 Главное меню «Сносер Няшка»", kb_main())
        try:
            await callback.answer()
        except Exception:
            pass

    @dp.callback_query(F.data == "menu:profile")
    async def cb_profile(callback: CallbackQuery) -> None:
        user_id = callback.from_user.id
        profile = store.get_or_create_user(user_id)
        contrib = store.get_contrib(user_id)
        admin_user = user_id in settings.trusted_admin_ids
        visible_plan = "админ" if admin_user else (profile.sub_plan or "нет")
        sub_until = "Без срока" if visible_plan in ("lifetime", "админ") else time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(profile.sub_until)
        )
        text = (
            f"Тариф: {visible_plan}\n"
            f"Активна: {'да' if user_has_moderation_access(user_id, settings.trusted_admin_ids, profile) else 'нет'}\n"
            f"Действует до: {sub_until}\n"
            f"Баллы: {profile.points}\n"
            f"Валидных сессий: {contrib['valid_sessions']}\n"
            f"Прокси (для веб-формы): {contrib['valid_proxies']}\n"
            "Обмен: 10 баллов = 1 неделя подписки ⭐"
        )
        await send_banner(callback.message, "profile", text, kb_profile(admin_user))
        try:
            await callback.answer()
        except Exception:
            pass

    @dp.callback_query(F.data == "menu:buy")
    async def cb_buy_menu(callback: CallbackQuery) -> None:
        await send_banner(callback.message, "buy", "💳 Выберите тариф подписки:", kb_buy())
        try:
            await callback.answer()
        except Exception:
            pass

    @dp.callback_query(F.data == "menu:stats")
    async def cb_stats(callback: CallbackQuery) -> None:
        stats = store.get_stats()
        sessions_live = len(engine.sessions) if engine else 0
        text = (
            "📊 Статистика системы\n\n"
            f"Рабочих сессий: {sessions_live}\n"
            f"Прокси в пуле (веб-форма): {proxy_pool.count}\n\n"
            "Атаки/модерации и новые пользователи:\n"
            f"За день: {stats['day']['attacks']} / {stats['day']['users']}\n"
            f"За неделю: {stats['week']['attacks']} / {stats['week']['users']}\n"
            f"За месяц: {stats['month']['attacks']} / {stats['month']['users']}\n"
            f"За год: {stats['year']['attacks']} / {stats['year']['users']}\n"
            f"За все время: {stats['all']['attacks']} / {stats['all']['users']}"
        )
        await send_banner(callback.message, "stats", text, kb_back())
        try:
            await callback.answer()
        except Exception:
            pass

    @dp.callback_query(F.data.startswith("buy:"))
    async def cb_buy(callback: CallbackQuery) -> None:
        action = callback.data.split(":")[1]
        user_id = callback.from_user.id
        if action == "check":
            row = store.get_last_unpaid_invoice(user_id)
            if not row:
                await callback.message.answer("Нет ожидающих инвойсов.")
                await callback.answer()
                return
            activated = await try_activate_invoice(row)
            if activated:
                await callback.message.answer("Оплата подтверждена. Подписка активирована.")
            else:
                await callback.message.answer("Инвойс еще не оплачен.")
            await callback.answer()
            return

        if action not in PLAN_PRICES:
            await callback.answer()
            return
        if not crypto.enabled:
            await callback.message.answer("Оплаты отключены. Укажите CRYPTO_BOT_TOKEN.")
            await callback.answer()
            return
        price = PLAN_PRICES[action]
        payload = f"{user_id}:{action}:{int(time.time())}"
        try:
            invoice = await crypto.create_invoice(price, payload, f"Compliance subscription: {PLAN_LABELS[action]}")
            invoice_id = str(invoice["invoice_id"])
            pay_url = invoice["pay_url"]
            store.create_invoice(user_id, action, price, invoice_id, pay_url)
            await callback.message.answer(
                f"Инвойс создан: {PLAN_LABELS[action]} / {price} USDT\nОплатить: {pay_url}\nПосле оплаты нажмите 'Проверить оплату'."
            )
        except Exception as exc:
            await callback.message.answer(f"Не удалось создать инвойс: {type(exc).__name__}")
        await callback.answer()

    @dp.callback_query(F.data == "menu:add_proxy")
    async def cb_add_proxy(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(MenuState.waiting_proxy_input)
        await send_banner(
            callback.message,
            "add_proxy",
            "🧩 Отправьте прокси строками или .txt.\n"
            "Они используются только для жалоб через сайт telegram.org/support (форма и письмо на сайте).",
            kb_back(),
        )
        await callback.answer()

    @dp.callback_query(F.data == "menu:add_session")
    async def cb_add_session(callback: CallbackQuery, state: FSMContext) -> None:
        if not is_engine_ready():
            await callback.message.answer("⚠️ Добавление сессий недоступно: не настроены API_ID/API_HASH на сервере.")
            await callback.answer()
            return
        await state.set_state(MenuState.waiting_session_file)
        await send_banner(
            callback.message,
            "add_session",
            "📁 Отправьте .session файл.\nЕсли сессия новая и рабочая, вы получите +1 балл.",
            kb_back(),
        )
        await callback.answer()

    @dp.callback_query(F.data == "menu:moderation")
    async def cb_moderation(callback: CallbackQuery, state: FSMContext) -> None:
        profile = store.get_or_create_user(callback.from_user.id)
        if not user_has_moderation_access(callback.from_user.id, settings.trusted_admin_ids, profile):
            await callback.message.answer("🔒 Нужна активная подписка. Купите тариф или накопите баллы.")
            await callback.answer()
            return
        await state.clear()
        await state.set_state(MenuState.waiting_moderation_route)
        await send_banner(
            callback.message,
            "moderation",
            "Выберите способ модерации:",
            kb_moderation_route(),
        )
        await callback.answer()

    @dp.callback_query(MenuState.waiting_moderation_route, F.data.startswith("modroute:"))
    async def on_moderation_route(callback: CallbackQuery, state: FSMContext) -> None:
        route = callback.data.split(":")[1]
        if route == "api":
            if not is_engine_ready():
                await callback.message.answer(
                    "⚠️ Репорты через API недоступны: не настроены API_ID/API_HASH на сервере. Выберите веб-форму."
                )
                await callback.answer()
                return
            await state.set_state(MenuState.waiting_moderation_target)
            await send_banner(
                callback.message,
                "moderation",
                "Шаг 1/5: отправьте цель (канал/пользователь/сообщение).\nПример: @channel, @username, https://t.me/channel/123",
                kb_back(),
            )
        else:
            await state.set_state(MenuState.waiting_web_proxy)
            await callback.message.answer(
                "Жалоба через сайт telegram.org/support.\n\nИспользовать прокси из пула?",
                reply_markup=kb_web_form_proxy_confirm(),
            )
        await callback.answer()

    @dp.callback_query(MenuState.waiting_web_proxy, F.data.startswith("webproxy:"))
    async def on_web_proxy_choice(callback: CallbackQuery, state: FSMContext) -> None:
        use = callback.data.endswith(":1")
        if use:
            proxy_pool.reload()
            if proxy_pool.count == 0:
                await callback.message.answer(
                    "В пуле нет прокси. Добавьте их через «Добавить мой прокси» или выберите «Нет, с IP сервера»."
                )
                await callback.answer()
                return
        await state.update_data(web_use_proxy=use)
        await state.set_state(MenuState.waiting_web_entity_kind)
        await callback.message.answer(
            "Выберите тип объекта:",
            reply_markup=kb_web_entity_kind(),
        )
        await callback.answer()

    @dp.callback_query(MenuState.waiting_web_entity_kind, F.data.startswith("webent:"))
    async def on_web_entity_kind(callback: CallbackQuery, state: FSMContext) -> None:
        kind = callback.data.split(":")[1]
        if kind not in ("user", "channel"):
            await callback.answer()
            return
        await state.update_data(web_entity_kind=kind)
        if kind == "user":
            await state.set_state(MenuState.waiting_web_user_id)
            await callback.message.answer("Введите числовой Telegram ID нарушителя (только цифры):")
        else:
            await state.set_state(MenuState.waiting_web_channel_link)
            await callback.message.answer("Введите ссылку на канал (например https://t.me/username):")
        await callback.answer()

    @dp.message(MenuState.waiting_web_user_id, F.text)
    async def on_web_user_id(message: Message, state: FSMContext) -> None:
        uid = message.text.strip()
        if not uid.isdigit():
            await message.answer("ID должен содержать только цифры.")
            return
        await state.update_data(web_user_id=uid)
        await state.set_state(MenuState.waiting_web_message_link)
        await message.answer("Введите ссылку на сообщение с явным нарушением (https://t.me/...):")

    @dp.message(MenuState.waiting_web_channel_link, F.text)
    async def on_web_channel_link(message: Message, state: FSMContext) -> None:
        link = message.text.strip().lower()
        if "t.me/" not in link and "telegram.me/" not in link:
            await message.answer("Нужна ссылка на t.me или telegram.me")
            return
        await state.update_data(web_channel_link=message.text.strip())
        await state.set_state(MenuState.waiting_web_message_link)
        await message.answer("Введите ссылку на сообщение с нарушением из этого канала:")

    @dp.message(MenuState.waiting_web_message_link, F.text)
    async def on_web_message_link(message: Message, state: FSMContext) -> None:
        link = message.text.strip().lower()
        if "t.me/" not in link and "telegram.me/" not in link:
            await message.answer("Нужна ссылка на сообщение (t.me/.../n).")
            return
        await state.update_data(web_message_link=message.text.strip())
        await state.set_state(MenuState.waiting_web_reason)
        await message.answer("Выберите причину жалобы:", reply_markup=kb_reasons())

    @dp.callback_query(MenuState.waiting_web_reason, F.data.startswith("reason:"))
    async def on_web_reason(callback: CallbackQuery, state: FSMContext) -> None:
        reason = callback.data.split(":", 1)[1]
        if reason not in REPORT_REASONS:
            await callback.answer("Неверная причина", show_alert=True)
            return
        data = await state.get_data()
        kind = data["web_entity_kind"]
        msg_link = data["web_message_link"]
        try:
            letter = build_web_letter(
                reason,
                entity_kind=kind,
                user_id=data.get("web_user_id", ""),
                channel_link=data.get("web_channel_link", ""),
                message_link=msg_link,
            )
        except FileNotFoundError as exc:
            await callback.message.answer(str(exc))
            await state.clear()
            await callback.answer()
            return
        await callback.answer()
        proxy_pool.reload()
        want_proxy = bool(data.get("web_use_proxy"))
        p = None
        if want_proxy and proxy_pool.count:
            p = proxy_pool.pick("support", settings.proxy_mode)
        route_note = f"Через прокси {p.scheme}://{p.host}:{p.port}." if p else "С IP сервера."
        await callback.message.answer(
            f"Открываю браузер ({route_note}) Пройдите Cloudflare (Turnstile) при необходимости; "
            "кнопка «Отправить» активируется после проверки на сайте."
        )
        ok, info = await submit_telegram_support_form(
            message=letter,
            legal_name=random_full_name_ru(),
            email=random_email(),
            phone=random_phone(),
            headless=settings.support_form_headless,
            captcha_timeout_ms=settings.support_captcha_timeout_ms,
            proxy=p,
        )
        uid = callback.from_user.id if callback.from_user else 0
        store.add_moderation_event(uid, 1)
        await callback.message.answer(("✅ " if ok else "❌ ") + info)
        await state.clear()

    @dp.message(MenuState.waiting_proxy_input, F.text)
    async def on_proxy_text(message: Message, state: FSMContext) -> None:
        result = await process_proxy_text(message.text, message.from_user.id, proxy_pool, store)
        if result["parsed"] == 0:
            await message.answer("Не удалось распознать прокси.")
            return
        await message.answer(
            f"Распознано: {result['parsed']}\nДобавлено новых: {result['added']}\n"
            f"Всего ваших прокси: {result['total']}\nНачислено баллов: {result['awarded']}"
        )
        await state.clear()

    @dp.message(MenuState.waiting_proxy_input, F.document)
    async def on_proxy_file(message: Message, state: FSMContext) -> None:
        if not message.document.file_name.lower().endswith(".txt"):
            await message.answer("Отправьте только .txt файл.")
            return
        temp = LOGS_DIR / f"proxy_upload_{message.from_user.id}_{int(time.time())}.txt"
        await message.bot.download(message.document, destination=temp)
        text = temp.read_text(encoding="utf-8", errors="ignore")
        temp.unlink(missing_ok=True)
        result = await process_proxy_text(text, message.from_user.id, proxy_pool, store)
        if result["parsed"] == 0:
            await message.answer("Не удалось распознать прокси.")
            return
        await message.answer(
            f"Распознано: {result['parsed']}\nДобавлено новых: {result['added']}\n"
            f"Всего ваших прокси: {result['total']}\nНачислено баллов: {result['awarded']}"
        )
        await state.clear()

    @dp.message(MenuState.waiting_moderation_target, F.text)
    async def on_moderation_target(message: Message, state: FSMContext) -> None:
        target = message.text.strip()
        if not target:
            await message.answer("Цель не может быть пустой.")
            return
        await state.update_data(target=target)
        await state.set_state(MenuState.waiting_moderation_reason)
        await send_banner(message, "moderation", "Шаг 2/5: выберите причину", kb_reasons())

    @dp.callback_query(MenuState.waiting_moderation_reason, F.data.startswith("reason:"))
    async def on_moderation_reason(callback: CallbackQuery, state: FSMContext) -> None:
        reason = callback.data.split(":", 1)[1]
        if reason not in REPORT_REASONS:
            await callback.answer("Неверная причина", show_alert=True)
            return
        await state.update_data(reason=reason)
        await state.set_state(MenuState.waiting_moderation_subreason)
        await callback.message.answer("Шаг 3/5: выберите подпричину", reply_markup=kb_subreasons(reason))
        await callback.answer()

    @dp.callback_query(MenuState.waiting_moderation_subreason, F.data.startswith("subreason:"))
    async def on_moderation_subreason(callback: CallbackQuery, state: FSMContext) -> None:
        data = await state.get_data()
        reason = data["reason"]
        idx = int(callback.data.split(":", 1)[1])
        subs = SUBREASONS.get(reason, ["Иное"])
        if idx < 0 or idx >= len(subs):
            await callback.answer("Неверная подпричина", show_alert=True)
            return
        await state.update_data(subreason=subs[idx])
        await state.set_state(MenuState.waiting_moderation_comment)
        await callback.message.answer("Шаг 4/5: добавьте комментарий к жалобе (или отправьте '-')")
        await callback.answer()

    @dp.message(MenuState.waiting_moderation_comment, F.text)
    async def on_moderation_comment(message: Message, state: FSMContext) -> None:
        comment = message.text.strip()
        if comment == "-":
            comment = "Без комментария"
        await state.update_data(comment=comment)
        await state.set_state(MenuState.waiting_moderation_count)
        await message.answer("Шаг 5/5: отправьте количество репортов (целое число ≥ 1, без верхнего лимита)")

    @dp.message(MenuState.waiting_moderation_count, F.text)
    async def on_moderation_count(message: Message, state: FSMContext) -> None:
        if not engine:
            await message.answer("⚠️ Модерация недоступна на этом сервере. Обратитесь в тех. поддержку.")
            await state.clear()
            return
        try:
            count = int(message.text.strip())
        except ValueError:
            await message.answer("Количество должно быть числом.")
            return
        if count < 1:
            await message.answer("Количество должно быть не меньше 1.")
            return
        data = await state.get_data()
        target = data["target"]
        reason = data["reason"]
        subreason = data.get("subreason", "Иное")
        comment = data.get("comment", "Без комментария")
        await engine.enqueue_reports(
            target=target,
            reason_key=reason,
            count=count,
            text=f"Причина: {REASON_LABELS.get(reason, reason)} / {subreason}. Комментарий: {comment}",
        )
        store.add_moderation_event(message.from_user.id, count)
        await message.answer(f"✅ Поставлено в очередь {count} задач модерации.")
        await engine.process_queue(lambda status: message.answer(status))
        await state.clear()

    @dp.message(MenuState.waiting_session_file, F.document)
    async def on_session_file(message: Message, state: FSMContext) -> None:
        if not engine:
            await message.answer("⚠️ Добавление сессий недоступно: не настроены API_ID/API_HASH.")
            await state.clear()
            return
        file_name = message.document.file_name
        if not file_name.lower().endswith(".session"):
            await message.answer("Пожалуйста, отправьте .session файл")
            return
        incoming = LOGS_DIR / f"incoming_{message.from_user.id}_{int(time.time())}.session"
        await message.bot.download(message.document, destination=incoming)
        fingerprint = hashlib.sha256(incoming.read_bytes()).hexdigest()
        accepted, points, total_sessions = store.register_session(message.from_user.id, fingerprint)
        if not accepted:
            incoming.unlink(missing_ok=True)
            await message.answer("Эта сессия уже была добавлена ранее.")
            return
        target = SESSIONS_DIR / f"user_{message.from_user.id}_{fingerprint[:8]}.session"
        incoming.replace(target)
        stats = await engine.reload()
        live_names = {s.name for s in engine.sessions}
        if target.stem not in live_names:
            target.unlink(missing_ok=True)
            await message.answer("Файл сессии сохранен, но сессия невалидна. Награда не начислена.")
            await state.clear()
            return
        await message.answer(f"Сессия добавлена и валидна. +{points} балл. Ваших валидных сессий: {total_sessions}")
        await state.clear()

    @dp.callback_query(F.data == "profile:redeem")
    async def cb_redeem(callback: CallbackQuery) -> None:
        res = store.consume_points_for_week(callback.from_user.id, points_cost=10)
        if not res:
            await callback.message.answer("Нужно 10 баллов для обмена на бесплатную неделю.")
        else:
            await callback.message.answer("Обмен выполнен: +1 неделя подписки.")
        await callback.answer()

    @dp.callback_query(F.data == "profile:promo")
    async def cb_promo_enter(callback: CallbackQuery, state: FSMContext) -> None:
        await state.set_state(MenuState.waiting_promo_code)
        await callback.message.answer("🎁 Отправьте промокод текстом:")
        await callback.answer()

    @dp.message(MenuState.waiting_promo_code, F.text)
    async def on_promo_code(message: Message, state: FSMContext) -> None:
        ok, text = store.redeem_promo_code(message.from_user.id, message.text.strip())
        await message.answer(("✅ " if ok else "❌ ") + text)
        await state.clear()

    @dp.callback_query(F.data == "admin:panel")
    async def cb_admin_panel(callback: CallbackQuery) -> None:
        if callback.from_user.id not in settings.trusted_admin_ids:
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Создать промокод", callback_data="admin:create_promo")],
                [InlineKeyboardButton(text="Назад", callback_data="menu:profile")],
            ]
        )
        await callback.message.answer("🛠 Админ панель", reply_markup=kb)
        await callback.answer()

    @dp.callback_query(F.data == "admin:create_promo")
    async def cb_admin_create_promo(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.from_user.id not in settings.trusted_admin_ids:
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await state.set_state(MenuState.waiting_admin_promo)
        await callback.message.answer(
            "Формат:\nCODE PLAN USES DAYS\nПример:\nNYA2026 month 100 30\nPLAN: week/month/year/lifetime"
        )
        await callback.answer()

    @dp.message(MenuState.waiting_admin_promo, F.text)
    async def on_admin_create_promo(message: Message, state: FSMContext) -> None:
        if message.from_user.id not in settings.trusted_admin_ids:
            await message.answer("Недостаточно прав")
            await state.clear()
            return
        try:
            code, plan, uses, days = message.text.strip().split()
            uses_i = int(uses)
            days_i = int(days)
            if plan not in PLAN_PRICES:
                raise ValueError("plan")
            expires_at = int(time.time()) + days_i * 86400
            ok = store.create_promo_code(code=code, plan=plan, uses_left=uses_i, expires_at=expires_at, created_by=message.from_user.id)
            if not ok:
                await message.answer("❌ Такой промокод уже существует.")
            else:
                await message.answer(f"✅ Промокод {code.upper()} создан.")
        except Exception:
            await message.answer("❌ Неверный формат. Пример: NYA2026 month 100 30")
        await state.clear()

    @dp.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        if not is_admin(message.from_user.id if message.from_user else None, settings.trusted_admin_ids):
            await message.answer("Доступ запрещен")
            return
        sessions_live = len(engine.sessions) if engine else 0
        queue_size = engine.queue.qsize() if engine else 0
        await message.answer(
            f"Живых сессий: {sessions_live}\nРазмер очереди: {queue_size}"
        )

    @dp.message(Command("reload"))
    async def cmd_reload(message: Message) -> None:
        if not is_admin(message.from_user.id if message.from_user else None, settings.trusted_admin_ids):
            await message.answer("Доступ запрещен")
            return
        if not engine:
            await message.answer("⚠️ Reload Telethon-движка недоступен: не настроены API_ID/API_HASH.")
            return
        stats = await engine.reload()
        await message.answer(f"Обновлено: {stats}")

    async def periodic_health_check() -> None:
        while not stop_bg.is_set():
            try:
                await asyncio.wait_for(stop_bg.wait(), timeout=float(settings.validation_interval_sec))
                return
            except asyncio.TimeoutError:
                pass
            if stop_bg.is_set():
                break
            try:
                if engine:
                    removed = await engine.validate_and_prune_sessions()
                    if removed:
                        logger.info("Health check: archived invalid sessions: %s", removed)
            except Exception as exc:
                logger.warning("Health check failed: %s", exc)

    async def run_polling_with_retries() -> None:
        failures = 0
        while True:
            try:
                me = await bot.get_me()
                logger.info("Bot API доступен. @%s", me.username)
                failures = 0
                await dp.start_polling(bot)
                return
            except (TelegramNetworkError, aiohttp.ClientError, OSError, asyncio.TimeoutError) as exc:
                failures += 1
                delay = min(2 ** min(failures, 8), 120)
                detail = str(exc).strip() or repr(exc)
                logger.warning(
                    "Ошибка сети Bot API: %s | %s. Попытка %s, пауза %ss. "
                    "ПК не достучался до api.telegram.org (блокировка/фаервол/DNS). "
                    "Проверка: python check_telegram.py",
                    type(exc).__name__,
                    detail[:200],
                    failures,
                    delay,
                )
                await asyncio.sleep(delay)

    watcher_task = asyncio.create_task(payments_watcher())
    health_task = asyncio.create_task(periodic_health_check())
    try:
        await run_polling_with_retries()
    finally:
        stop_bg.set()
        watcher_task.cancel()
        health_task.cancel()
        await asyncio.gather(watcher_task, health_task, return_exceptions=True)
        if engine:
            await engine.disconnect_all()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
