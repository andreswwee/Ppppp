from __future__ import annotations

import asyncio
import logging
import os
import re

import aiohttp
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set in environment")

HEADERS = {"User-Agent": "CronusOSINT/2.0"}
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{3,32}$")
DOMAIN_RE = re.compile(r"^[a-zA-Z0-9\-]+(\.[a-zA-Z0-9\-]+)+$")

router = Router()
logging.basicConfig(level=logging.INFO)


# ── Публичные источники: проверка существования аккаунта ─────────────

async def src_github(session: aiohttp.ClientSession, u: str) -> str | None:
    async with session.get(f"https://api.github.com/users/{u}", headers=HEADERS) as r:
        if r.status == 200:
            d = await r.json()
            return f"GitHub: https://github.com/{u} (followers: {d.get('followers', 0)})"
    return None


async def src_reddit(session: aiohttp.ClientSession, u: str) -> str | None:
    async with session.get(f"https://www.reddit.com/user/{u}/about.json", headers=HEADERS) as r:
        if r.status == 200:
            return f"Reddit: https://www.reddit.com/user/{u}"
    return None


async def src_hn(session: aiohttp.ClientSession, u: str) -> str | None:
    async with session.get(f"https://hn.algolia.com/api/v1/users/{u}", headers=HEADERS) as r:
        if r.status == 200:
            d = await r.json()
            if d.get("created_at"):
                return f"Hacker News: https://news.ycombinator.com/user?id={u}"
    return None


async def src_telegram(session: aiohttp.ClientSession, u: str) -> str | None:
    async with session.get(f"https://t.me/{u}", headers=HEADERS) as r:
        if r.status == 200:
            text = await r.text()
            if "tgme_page_extra" in text:  # публичная карточка профиля существует
                return f"Telegram: https://t.me/{u}"
    return None


async def src_steam(session: aiohttp.ClientSession, u: str) -> str | None:
    async with session.get(f"https://steamcommunity.com/id/{u}", headers=HEADERS) as r:
        if r.status == 200:
            return f"Steam: https://steamcommunity.com/id/{u}"
    return None


SOURCES = [src_github, src_reddit, src_hn, src_telegram, src_steam]


async def enumerate_nick(session: aiohttp.ClientSession, u: str) -> list[str]:
    """Параллельный опрос всех источников. Ошибки сети не валят весь прогон."""
    results = await asyncio.gather(
        *[src(session, u) for src in SOURCES], return_exceptions=True
    )
    return [r for r in results if isinstance(r, str)]


# ── Полный публичный профиль GitHub ──────────────────────────────────

async def github_report(session: aiohttp.ClientSession, u: str) -> str | None:
    async with session.get(f"https://api.github.com/users/{u}", headers=HEADERS) as r:
        if r.status != 200:
            return None
        d = await r.json()
    lines = [f"Цель: @{u}", ""]
    for key, label in [
        ("name", "Имя"), ("company", "Компания"), ("location", "Локация"),
        ("email", "Email"), ("bio", "Био"), ("blog", "Сайт"),
        ("public_repos", "Репозиториев"), ("followers", "Подписчиков"),
        ("following", "Подписок"), ("created_at", "Создан"),
    ]:
        if d.get(key):
            lines.append(f"{label}: {d[key]}")
    lines.append(f"Профиль: {d.get('html_url')}")
    return "\n".join(lines)


# ── Публичный доменный реестр (RDAP) ─────────────────────────────────

async def whois_report(session: aiohttp.ClientSession, domain: str) -> str | None:
    async with session.get(f"https://rdap.org/domain/{domain}", headers=HEADERS) as r:
        if r.status != 200:
            return None
        d = await r.json()

    lines = [f"Домен: {domain}", ""]
    if d.get("status"):
        lines.append("Статус: " + ", ".join(d["status"]))
    for e in d.get("events", []):
        if e.get("eventAction") in ("registration", "expiration", "last changed"):
            lines.append(f"{e['eventAction']}: {e['eventDate']}")

    registrar = None
    for ent in d.get("entities", []):
        vc = ent.get("vcardArray")
        if not vc or len(vc) < 2:
            continue
        for field in vc[1]:
            if field[0] == "fn":
                registrar = field[3]
                break
        if registrar:
            break
    if registrar:
        lines.append(f"Регистратор: {registrar}")

    ns = [s.get("ldhName") for s in d.get("nameservers", []) if s.get("ldhName")]
    if ns:
        lines.append("NS: " + ", ".join(ns[:4]))
    return "\n".join(lines)


# ── Хэндлеры ──────────────────────────────────────────────────────────

def get_arg(message: Message) -> str:
    parts = message.text.split(maxsplit=1)
    return parts[1].strip().lstrip("@") if len(parts) > 1 else ""


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Cronus OSINT v2. Только публичные данные.\n"
        "/nick <username> — поиск аккаунтов по нику на 5 платформах\n"
        "/github <username> — полный публичный профиль GitHub\n"
        "/whois <domain> — публичный доменный реестр"
    )


@router.message(Command("nick"))
async def cmd_nick(message: Message):
    u = get_arg(message)
    if not USERNAME_RE.match(u):
        return await message.answer("Формат: /nick username (латиница, 3-32 символа)")
    async with aiohttp.ClientSession() as session:
        hits = await enumerate_nick(session, u)
    if hits:
        await message.answer(f"Цель: {u}\n\n" + "\n".join(f"• {h}" for h in hits))
    else:
        await message.answer(f"{u}: публичных аккаунтов в monitored-источниках нет.")


@router.message(Command("github"))
async def cmd_github(message: Message):
    u = get_arg(message)
    if not USERNAME_RE.match(u):
        return await message.answer("Формат: /github username")
    async with aiohttp.ClientSession() as session:
        report = await github_report(session, u)
    await message.answer(report or f"{u}: не найден в GitHub.")


@router.message(Command("whois"))
async def cmd_whois(message: Message):
    domain = get_arg(message).lower()
    if not DOMAIN_RE.match(domain):
        return await message.answer("Формат: /whois example.com")
    async with aiohttp.ClientSession() as session:
        report = await whois_report(session, domain)
    await message.answer(report or f"{domain}: реестр не ответил или домен скрыт.")


async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
