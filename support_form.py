from __future__ import annotations

import random
import re
import string
from typing import Optional

from config import TEXTS_DIR
from proxy_handler import ProxyConfig


def load_reason_template(reason_key: str) -> str:
    path = TEXTS_DIR / f"{reason_key}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Шаблон не найден: {path}")
    return path.read_text(encoding="utf-8")


def build_support_letter(
    reason_key: str,
    *,
    report_user_id: str = "",
    message_link: str = "",
    channel_link: str = "",
) -> str:
    raw = load_reason_template(reason_key)
    return (
        raw.replace("{report_user_id}", report_user_id.strip())
        .replace("{message_link}", message_link.strip())
        .replace("{channel_link}", channel_link.strip())
    )


def build_web_letter(
    reason_key: str,
    *,
    entity_kind: str,
    user_id: str = "",
    channel_link: str = "",
    message_link: str = "",
) -> str:
    if entity_kind == "user":
        return build_support_letter(
            reason_key,
            report_user_id=user_id,
            message_link=message_link,
            channel_link="не применимо",
        )
    prefix = (
        "ВАЖНО: обращение касается канала; идентификатор пользователя в жалобе не используется. "
        "Ниже указаны ссылка на канал и ссылка на сообщение с нарушением.\n\n"
    )
    return prefix + build_support_letter(
        reason_key,
        report_user_id="не применимо (канал)",
        message_link=message_link,
        channel_link=channel_link,
    )


_FIRST = (
    "Александр",
    "Дмитрий",
    "Максим",
    "Сергей",
    "Андрей",
    "Алексей",
    "Иван",
    "Евгений",
    "Михаил",
    "Владимир",
    "Николай",
    "Павел",
    "Роман",
    "Игорь",
    "Константин",
)
_PATR = (
    "Александрович",
    "Дмитриевич",
    "Сергеевич",
    "Андреевич",
    "Владимирович",
    "Игоревич",
    "Николаевич",
    "Павлович",
    "Романович",
    "Евгеньевич",
    "Михайлович",
    "Олегович",
)
_LAST = (
    "Смирнов",
    "Иванов",
    "Кузнецов",
    "Попов",
    "Соколов",
    "Лебедев",
    "Козлов",
    "Новиков",
    "Морозов",
    "Петров",
    "Волков",
    "Соловьёв",
    "Васильев",
    "Зайцев",
    "Павлов",
    "Семёнов",
    "Голубев",
    "Виноградов",
    "Богданов",
    "Фёдоров",
)


def random_full_name_ru() -> str:
    return f"{random.choice(_FIRST)} {random.choice(_PATR)} {random.choice(_LAST)}"


def random_email() -> str:
    alphabet = string.ascii_lowercase + string.digits
    user = "".join(random.choices(alphabet, k=random.randint(9, 14)))
    domain = random.choice(("gmail.com", "hotmail.com", "rambler.ru", "mail.ru"))
    return f"{user}@{domain}"


def random_phone() -> str:
    cc = random.choice(("1", "7", "44", "49", "33", "380"))
    rest = "".join(str(random.randint(0, 9)) for _ in range(10))
    return f"+{cc}{rest}"


def submit_telegram_support_form_sync(
    *,
    message: str,
    legal_name: str,
    email: str,
    phone: str,
    headless: bool,
    lang: str = "ru",
    captcha_timeout_ms: int = 600_000,
    proxy: Optional[ProxyConfig] = None,
) -> tuple[bool, str]:
    """
    Заполняет https://telegram.org/support и ждёт, пока кнопка отправки станет активной
    (после успешного Cloudflare Turnstile на стороне сайта). Автоматически «обойти»
    Turnstile нельзя; в режиме с видимым браузером пользователь может пройти проверку вручную.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return False, f"Установите playwright: pip install playwright && playwright install chromium ({exc})"

    url = f"https://telegram.org/support?setln={lang}"
    pw_proxy = proxy.as_playwright_dict() if proxy else None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(proxy=pw_proxy) if pw_proxy else browser.new_context()
        try:
            page = context.new_page()
            page.set_default_timeout(90_000)
            page.goto(url, wait_until="domcontentloaded")
            page.fill('textarea[name="message"]', message)
            page.fill('input[name="legal_name"]', legal_name)
            page.fill('input[name="email"]', email)
            page.fill('input[name="phone"]', phone)
            try:
                page.wait_for_selector(
                    "button.js-captcha-controlled-btn:not([disabled])",
                    timeout=captcha_timeout_ms,
                )
            except Exception as exc:
                return (
                    False,
                    "Проверка Cloudflare (Turnstile) не завершена за отведённое время или страница изменилась. "
                    f"Откройте форму вручную при SUPPORT_FORM_HEADLESS=0 и пройдите капчу. ({type(exc).__name__})",
                )
            page.click('button[type="submit"]')
            page.wait_for_load_state("domcontentloaded", timeout=120_000)
            text = page.inner_text("body")
            if re.search(r"thank you|спасибо|received|получен", text, re.I):
                return True, "Форма отправлена (по тексту страницы похоже на успех)."
            return True, "Запрос отправлен (проверьте ответ на странице)."
        finally:
            context.close()
            browser.close()


async def submit_telegram_support_form(
    *,
    message: str,
    legal_name: str,
    email: str,
    phone: str,
    headless: bool,
    lang: str = "ru",
    captcha_timeout_ms: int = 600_000,
    proxy: Optional[ProxyConfig] = None,
) -> tuple[bool, str]:
    import asyncio

    return await asyncio.to_thread(
        submit_telegram_support_form_sync,
        message=message,
        legal_name=legal_name,
        email=email,
        phone=phone,
        headless=headless,
        lang=lang,
        captcha_timeout_ms=captcha_timeout_ms,
        proxy=proxy,
    )
