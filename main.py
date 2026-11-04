
import asyncio

from curl_cffi.requests import AsyncSession
from dtek_client import DtekClient
from dtek_client.browser_auth import get_cleared_cookies


BASE_URL = "https://www.dtek-krem.com.ua"
SCHEDULE_URL = f"{BASE_URL}/ua/shutdowns"
AJAX_URL = f"{BASE_URL}/ua/ajax"

CITY = "с. Займище"
STREET = "вул. Центральна"
HOUSE = "21"


async def main():
    print("Отримання браузерної сесії ДТЕК...")

    cookies, csrf_token = await get_cleared_cookies(
        SCHEDULE_URL
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "uk,en;q=0.9",
        "Origin": BASE_URL,
        "Referer": SCHEDULE_URL,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    }

    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token

    session = AsyncSession(
        timeout=20.0,
        headers=headers,
        cookies=cookies,
        impersonate="chrome120",
    )

    print("Браузерну сесію отримано.")
    print(f"Перевіряємо: {CITY}, {STREET}, {HOUSE}")

    async with DtekClient(
        "krem",
        ajax_url=AJAX_URL,
        session=session,
    ) as client:

        result = await client.get_group_by_address(
            city=CITY,
            street=STREET,
            house_number=HOUSE,
        )

        print("\nРезультат:")
        print(f"Населений пункт: {result.city}")
        print(f"Вулиця: {result.street}")
        print(f"Будинок: {result.house_number}")
        print(f"Група: {result.group_display_name}")

        if "4.1" not in result.group_display_name:
            raise RuntimeError(
                "Група не збігається з очікуваною 4.1"
            )

        print("\nУСПІХ: адресу та групу підтверджено.")


if __name__ == "__main__":
    asyncio.run(main())
