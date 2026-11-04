
import asyncio
from dtek_client import DtekClient


CITY = "с. Займище"
STREET = "вул. Центральна"
HOUSE = "21"


async def main():
    print("Перевірка адреси ДТЕК")
    print(f"Адреса: {CITY}, {STREET}, {HOUSE}")

    async with DtekClient("krem") as client:
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
                "Увага: отримана група не збігається з очікуваною 4.1"
            )

        print("\nУСПІХ: адреса та черга 4.1 підтверджені")


if __name__ == "__main__":
    asyncio.run(main())
