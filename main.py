
async def get_dtek_schedule():
    print("Підключення до ДТЕК...")

    cookies, csrf_token = await get_cleared_cookies(
        SCHEDULE_URL
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "application/json, text/javascript, "
            "*/*; q=0.01"
        ),
        "Accept-Language": "uk,en;q=0.9",
        "Origin": BASE_URL,
        "Referer": SCHEDULE_URL,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": (
            "application/x-www-form-urlencoded; charset=UTF-8"
        ),
    }

    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token

    session = AsyncSession(
        timeout=20.0,
        headers=headers,
        cookies=cookies,
        impersonate="chrome120",
    )

    async with DtekClient(
        "krem",
        ajax_url=AJAX_URL,
        session=session,
    ) as client:

        print("Перевірка адреси ДТЕК...")
        print(f"Адреса: {CITY}, {STREET}, {HOUSE}")

        try:
            # Перевіряємо адресу
            address = await client.get_group_by_address(
                city=CITY,
                street=STREET,
                house_number=HOUSE,
            )

            print("Адресу успішно перевірено.")
            print("Результат:", object_to_dict(address))

            # Отримуємо інформацію про всі черги будинку
            print("\n========== ДІАГНОСТИКА ЧЕРГ ==========")

            response = await client.get_home_num(
                city=CITY,
                street=STREET,
            )

            house = response.houses.get(HOUSE)

            if house is None:
                print(f"Будинок {HOUSE} не знайдено.")
                print(
                    "Доступні будинки:",
                    list(response.houses.keys()),
                )
            else:
                print(f"Адреса: {CITY}, {STREET}, {HOUSE}")
                print(f"Черги будинку: {house.group_ids}")
                print(f"Основна черга: {house.primary_group}")
                print(f"Кілька черг: {house.is_multi_group}")
                print(
                    f"Поточне відключення: "
                    f"{house.has_current_outage}"
                )

                print("\nГРАФІКИ ЗА КОЖНОЮ ЧЕРГОЮ:")

                for group_id in house.group_ids:
                    print(f"\nЧерга {group_id}:")
                    print(
                        response.fact.get_group_today(group_id)
                    )

            print("========== КІНЕЦЬ ДІАГНОСТИКИ ==========\n")

            # Поки що залишаємо отримання графіків
            # старим способом. Це діагностичний запуск.
            print("Отримання графіка на сьогодні...")

            today = await client.get_today_schedule(
                city=CITY,
                street=STREET,
                house_number=HOUSE,
            )

            print("Отримання графіка на завтра...")

            tomorrow = await client.get_tomorrow_schedule(
                city=CITY,
                street=STREET,
                house_number=HOUSE,
            )

            return today, tomorrow

        except DtekDataError as error:
            print("ПОМИЛКА ДТЕК AJAX")
            print(f"Тип помилки: {type(error).__name__}")
            print(f"Повідомлення: {error}")
            print(
                "Відповідь сервера:",
                getattr(error, "raw", None),
            )
            raise
