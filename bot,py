import asyncio
import html
import ipaddress
import logging
import os
import re
import socket
import time
from collections import defaultdict
from typing import Optional 

import aiohttp
from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import BotCommand, Message


# ============================================================
# CONFIGURACIÓN
# ============================================================

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

RATE_LIMIT = int(os.getenv("RATE_LIMIT", "10"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "12"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("universal-search-bot")

router = Router()

DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}$",
    re.IGNORECASE,
)

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{2,64}$")

request_history: dict[int, list[float]] = defaultdict(list)


# ============================================================
# UTILIDADES
# ============================================================

def get_command_argument(message: Message) -> str:
    text = message.text or ""
    parts = text.split(maxsplit=1)

    if len(parts) != 2 or not parts[1].strip():
        raise ValueError("Falta el objetivo después del comando.")

    return parts[1].strip()


def check_rate_limit(user_id: int) -> bool:
    now = time.monotonic()

    recent_requests = [
        timestamp
        for timestamp in request_history[user_id]
        if now - timestamp < 60
    ]

    request_history[user_id] = recent_requests

    if len(recent_requests) >= RATE_LIMIT:
        return False

    recent_requests.append(now)
    return True


def classify_target(raw_value: str) -> tuple[str, str]:
    value = raw_value.strip().removeprefix("@")

    if not value:
        raise ValueError("El objetivo está vacío.")

    try:
        address = ipaddress.ip_address(value)
        return "ip", str(address)
    except ValueError:
        pass

    if DOMAIN_PATTERN.fullmatch(value):
        return "domain", value.lower()

    if USERNAME_PATTERN.fullmatch(value):
        return "username", value

    raise ValueError(
        "Objetivo inválido. Usa un username, dominio o dirección IP."
    )


def validate_forced_target(target_type: str, value: str) -> str:
    value = value.strip().removeprefix("@")

    if target_type == "username":
        if not USERNAME_PATTERN.fullmatch(value):
            raise ValueError("El valor no parece un username válido.")
        return value

    if target_type == "domain":
        if not DOMAIN_PATTERN.fullmatch(value):
            raise ValueError("El valor no parece un dominio válido.")
        return value.lower()

    if target_type == "ip":
        try:
            return str(ipaddress.ip_address(value))
        except ValueError as error:
            raise ValueError(
                "El valor no parece una dirección IP válida."
            ) from error

    raise ValueError("Tipo de búsqueda desconocido.")


def safe_text(value: object) -> str:
    return html.escape(str(value))


async def fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    headers: Optional[dict[str, str]] = None,
) -> Optional[dict]:
    try:
        async with session.get(
            url,
            headers=headers,
            allow_redirects=True,
        ) as response:
            if response.status != 200:
                logger.info("HTTP %s para %s", response.status, url)
                return None

            data = await response.json(content_type=None)

            if isinstance(data, dict):
                return data

    except asyncio.TimeoutError:
        logger.warning("Timeout consultando %s", url)

    except aiohttp.ClientError as error:
        logger.warning("Error HTTP consultando %s: %s", url, error)

    except ValueError:
        logger.warning("Respuesta JSON inválida desde %s", url)

    return None


# ============================================================
# BÚSQUEDA DE USERNAMES
# ============================================================

USERNAME_SOURCES = {
    "GitHub": "https://github.com/{username}",
    "GitLab": "https://gitlab.com/{username}",
    "Reddit": "https://www.reddit.com/user/{username}",
    "Keybase": "https://keybase.io/{username}",
    "DEV": "https://dev.to/{username}",
    "Codeberg": "https://codeberg.org/{username}",
}


async def check_public_profile(
    session: aiohttp.ClientSession,
    source: str,
    url: str,
) -> Optional[str]:
    try:
        async with session.get(
            url,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 UniversalSearchBot/1.0 "
                    "(public OSINT research)"
                )
            },
        ) as response:
            if response.status == 200:
                escaped_source = safe_text(source)
                escaped_url = html.escape(url, quote=True)

                return (
                    f'• <b>{escaped_source}</b>: '
                    f'<a href="{escaped_url}">perfil público encontrado</a>'
                )

    except asyncio.TimeoutError:
        logger.info("Timeout comprobando %s", source)

    except aiohttp.ClientError as error:
        logger.info("Error comprobando %s: %s", source, error)

    return None


async def search_username(
    username: str,
    session: aiohttp.ClientSession,
) -> list[str]:
    checks = [
        check_public_profile(
            session,
            source,
            template.format(username=username),
        )
        for source, template in USERNAME_SOURCES.items()
    ]

    results = await asyncio.gather(*checks)

    return [result for result in results if result is not None]


# ============================================================
# BÚSQUEDA DE DOMINIOS
# ============================================================

async def search_domain(
    domain: str,
    session: aiohttp.ClientSession,
) -> list[str]:
    results: list[str] = []

    dns_url = (
        "https://cloudflare-dns.com/dns-query"
        f"?name={domain}&type=A"
    )

    dns_data = await fetch_json(
        session,
        dns_url,
        headers={
            "Accept": "application/dns-json",
            "User-Agent": "UniversalSearchBot/1.0",
        },
    )

    if dns_data:
        addresses = sorted(
            {
                answer.get("data", "")
                for answer in dns_data.get("Answer", [])
                if answer.get("type") == 1 and answer.get("data")
            }
        )

        for address in addresses[:10]:
            results.append(
                f"• <b>DNS A</b>: <code>{safe_text(address)}</code>"
            )

    rdap_url = f"https://rdap.org/domain/{domain}"

    rdap_data = await fetch_json(
        session,
        rdap_url,
        headers={"User-Agent": "UniversalSearchBot/1.0"},
    )

    if rdap_data:
        domain_name = rdap_data.get("ldhName", domain)
        statuses = ", ".join(rdap_data.get("status", [])) or "sin datos"

        event_dates: dict[str, str] = {}

        for event in rdap_data.get("events", []):
            action = event.get("eventAction")
            date = event.get("eventDate")

            if action and date:
                event_dates[action] = date[:10]

        registration = event_dates.get(
            "registration",
            event_dates.get("last changed", "desconocido"),
        )

        expiration = event_dates.get("expiration", "desconocida")

        results.append(
            "• <b>RDAP</b>: "
            f"{safe_text(domain_name)}
"
            f"  Estado: {safe_text(statuses)}
"
            f"  Registro: {safe_text(registration)}
"
            f"  Expiración: {safe_text(expiration)}"
        )

    return results


# ============================================================
# BÚSQUEDA DE DIRECCIONES IP
# ============================================================

async def search_ip(
    raw_address: str,
    session: aiohttp.ClientSession,
) -> list[str]:
    address = ipaddress.ip_address(raw_address)

    if not address.is_global:
        return [
            "• <b>Dirección bloqueada</b>: "
            "no es una IP pública global."
        ]

    results: list[str] = []
    event_loop = asyncio.get_running_loop()

    try:
        hostname, aliases, _ = await event_loop.run_in_executor(
            None,
            socket.gethostbyaddr,
            raw_address,
        )

        results.append(
            f"• <b>DNS inverso</b>: "
            f"<code>{safe_text(hostname)}</code>"
        )

        for alias in aliases[:5]:
            results.append(
                f"• <b>Alias DNS</b>: "
                f"<code>{safe_text(alias)}</code>"
            )

    except (socket.herror, socket.gaierror, OSError):
        logger.info("Sin DNS inverso para %s", raw_address)

    rdap_url = f"https://rdap.org/ip/{raw_address}"

    rdap_data = await fetch_json(
        session,
        rdap_url,
        headers={"User-Agent": "UniversalSearchBot/1.0"},
    )

    if rdap_data:
        network_name = rdap_data.get("name", "sin nombre")
        country = rdap_data.get("country", "desconocido")
        start_address = rdap_data.get("startAddress", "?")
        end_address = rdap_data.get("endAddress", "?")
        network_type = rdap_data.get("type", "desconocido")

        results.append(
            "• <b>Asignación RDAP</b>\n"
            f"  Red: {safe_text(network_name)}
"
            f"  País: {safe_text(country)}
"
            f"  Tipo: {safe_text(network_type)}
"
            f"  Rango: <code>{safe_text(start_address)}"
            f" - {safe_text(end_address)}</code>"
        )

    return results


# ============================================================
# EJECUCIÓN DE BÚSQUEDAS
# ============================================================

async def execute_search(
    message: Message,
    forced_type: Optional[str] = None,
) -> None:
    if not message.from_user:
        return

    if not check_rate_limit(message.from_user.id):
        await message.answer(
            "Has alcanzado el límite temporal. "
            "Espera un minuto antes de volver a buscar."
        )
        return

    status_message: Optional[Message] = None

    try:
        raw_target = get_command_argument(message)

        if forced_type:
            target_type = forced_type
            target = validate_forced_target(forced_type, raw_target)
        else:
            target_type, target = classify_target(raw_target)

        status_message = await message.answer(
            "Buscando en fuentes públicas…"
        )

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

        async with aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": "UniversalSearchBot/1.0"},
        ) as session:
            if target_type == "username":
                results = await search_username(target, session)

            elif target_type == "domain":
                results = await search_domain(target, session)

            elif target_type == "ip":
                results = await search_ip(target, session)

            else:
                raise ValueError("Tipo de búsqueda no compatible.")

        response_lines = [
            f"<b>Universal Search: {safe_text(target_type)}</b>",
            f"<code>{safe_text(target)}</code>",
            "",
        ]

        if results:
            response_lines.extend(results)
        else:
            response_lines.append(
                "No se encontraron hallazgos públicos verificables."
            )

        response_lines.extend(
            [
                "",
                "<i>Un match no confirma identidad. "
                "Verifica los hallazgos manualmente.</i>",
            ]
        )

        response_text = "\n".join(response_lines)

        if len(response_text) > 4096:
            response_text = response_text[:3900]
            response_text += "\n\n<i>Resultado recortado por Telegram.</i>"

        await status_message.edit_text(
            response_text,
            disable_web_page_preview=True,
        )

    except ValueError as error:
        error_text = safe_text(error)

        if status_message:
            await status_message.edit_text(error_text)
        else:
            await message.answer(error_text)

    except asyncio.TimeoutError:
        logger.exception("Timeout general durante una búsqueda")

        error_text = (
            "La búsqueda tardó demasiado. "
            "Prueba nuevamente dentro de unos segundos."
        )

        if status_message:
            await status_message.edit_text(error_text)
        else:
            await message.answer(error_text)

    except Exception:
        logger.exception("Error inesperado durante una búsqueda")

        error_text = (
            "Ocurrió un error inesperado. "
            "Revisa los logs del contenedor."
        )

        if status_message:
            await status_message.edit_text(error_text)
        else:
            await message.answer(error_text)


# ============================================================
# COMANDOS DE TELEGRAM
# ============================================================

@router.message(Command("start", "help"))
async def start_command(message: Message) -> None:
    await message.answer(
        "<b>Universal Search OSINT</b>\n\n"
        "Consulta información disponible públicamente:\n\n"
        "<code>/search objetivo</code>\n"
        "<code>/username nombre</code>\n"
        "<code>/domain example.com</code>\n"
        "<code>/ip 1.1.1.1</code>\n"
        "<code>/privacy</code>\n\n"
        "Ejemplos:\n"
        "<code>/search torvalds</code>\n"
        "<code>/domain example.com</code>\n"
        "<code>/ip 1.1.1.1</code>\n\n"
        "Úsalo únicamente con fines legítimos y autorizados."
    )


@router.message(Command("search"))
async def search_command(message: Message) -> None:
    await execute_search(message)


@router.message(Command("username"))
async def username_command(message: Message) -> None:
    await execute_search(message, forced_type="username")


@router.message(Command("domain"))
async def domain_command(message: Message) -> None:
    await execute_search(message, forced_type="domain")


@router.message(Command("ip"))
async def ip_command(message: Message) -> None:
    await execute_search(message, forced_type="ip")


@router.message(Command("privacy"))
async def privacy_command(message: Message) -> None:
    await message.answer(
        "<b>Privacidad</b>\n\n"
        "El bot no guarda objetivos ni resultados en una base de datos.\n"
        "Solo mantiene temporalmente en memoria el número de consultas "
        "necesario para aplicar el límite de uso."
    )


# ============================================================
# INICIO DEL BOT
# ============================================================

async def main() -> None:
    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    await bot.set_my_commands(
        [
            BotCommand(
                command="search",
                description="Detectar y buscar un objetivo",
            ),
            BotCommand(
                command="username",
                description="Buscar un username público",
            ),
            BotCommand(
                command="domain",
                description="Consultar un dominio",
            ),
            BotCommand(
                command="ip",
                description="Consultar una IP pública",
            ),
            BotCommand(
                command="privacy",
                description="Información de privacidad",
            ),
            BotCommand(
                command="help",
                description="Mostrar ayuda",
            ),
        ]
    )

    logger.info("Iniciando Universal Search Bot")

    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
