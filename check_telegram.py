"""
Проверка доступа до Telegram Bot API с этой машины.
Запуск из папки проекта: python check_telegram.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys

import aiohttp
from dotenv import load_dotenv

load_dotenv()


async def run() -> None:
    host = "api.telegram.org"
    print("=== Резолв DNS", host, "===")
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        for fam, _, _, _, sockaddr in infos[:6]:
            fam_name = "IPv4" if fam == socket.AF_INET else "IPv6" if fam == socket.AF_INET6 else str(fam)
            print(f"  {fam_name}: {sockaddr}")
    except OSError as e:
        print("  ОШИБКА DNS/сокета:", e)
        return

    token = os.getenv("BOT_TOKEN", "").strip()
    url = f"https://api.telegram.org/bot{token}/getMe" if token else "https://api.telegram.org"
    timeout = aiohttp.ClientTimeout(total=30, connect=15)

    print("\n=== HTTPS без ограничения семейства (как по умолчанию) ===")
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url if token else "https://api.telegram.org") as resp:
                print("  Статус:", resp.status)
                if token:
                    body = await resp.text()
                    print("  Ответ (начало):", body[:120].replace("\n", " "))
    except Exception as e:
        print("  ОШИБКА:", type(e).__name__, e)

    print("\n=== HTTPS только IPv4 (как при BOT_API_FORCE_IPV4=1) ===")
    try:
        conn = aiohttp.TCPConnector(family=socket.AF_INET, ssl=True)
        async with aiohttp.ClientSession(timeout=timeout, connector=conn) as session:
            async with session.get(url if token else "https://api.telegram.org") as resp:
                print("  Статус:", resp.status)
                if token:
                    body = await resp.text()
                    print("  Ответ (начало):", body[:120].replace("\n", " "))
    except Exception as e:
        print("  ОШИБКА:", type(e).__name__, e)

    print(
        "\nЕсли оба варианта падают: проверьте фаервол, антивирус, блокировку Telegram у провайдера, "
        "смену DNS (например 1.1.1.1), другую сеть (телефон как точка доступа)."
    )


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())
