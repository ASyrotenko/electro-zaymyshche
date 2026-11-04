
import asyncio
from dtek_client import DtekClient

CITY = "с. Займище"
STREET = "вул. Центральна"
HOUSE = "21"

AJAX_URL = "https://www.dtek-krem.com.ua/ua/ajax"


async def main():
    print("Перевірка адреси ДТЕК")
    print(f"Адреса: {CITY}, {STREET}, {HOUSE}")
    print(f"AJAX URL: {AJAX_URL}")

    async with DtekClient(
        "krem",
        ajax_url=AJAX_URL
    ) as client:

        result = await client.get_group_by_address(
            city=CITY,
            street=STREET,
            house_number=HOUSE,
        )

        print("\nРезультат пошуку:")
        print(f"Населений пункт: {result.city}")
        print(f"Вулиця: {result.street}")
        print(f"Будинок: {result.house_number}")
        print(f"Група: {result.group_display_name}")
        print(f"ID групи: {result.group_id}")

        if "4.1" not in result.group_display_name:
            raise RuntimeError(
                "Отримана група не збігається з очікуваною 4.1"
            )

        print("\nУСПІХ: адресу та чергу підтверджено")


if __name__ == "__main__":
    asyncio.run(main())
