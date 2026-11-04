
import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from curl_cffi.requests import AsyncSession
from dtek_client import DtekClient
from dtek_client.browser_auth import get_cleared_cookies


# Налаштування ДТЕК
BASE_URL = "https://www.dtek-krem.com.ua"
SCHEDULE_URL = f"{BASE_URL}/ua/shutdowns"
AJAX_URL = f"{BASE_URL}/ua/ajax"

CITY = "с. Займище"
STREET = "вул. Центральна"
HOUSE = "21"
EXPECTED_GROUP = "4.1"

# Файл з попереднім графіком
STATE_FILE = Path("state.json")

KYIV_TZ = ZoneInfo("Europe/Kyiv")

STATUS_NAMES = {
    "YES": "Світло є",
    "NO": "Відключення",
    "MAYBE": "Можливе відключення",
    "FIRST": "Відключення в першій частині інтервалу",
    "SECOND": "Відключення в другій частині інтервалу",
    "MFIRST": "Можливе відключення в першій частині",
    "MSECOND": "Можливе відключення в другій частині",
    "UNKNOWN": "Статус невідомий",
}


def get_status_code(status):
    value = getattr(status, "value", None)

    if value is not None:
        return str(value).upper()

    name = getattr(status, "name", None)

    if name is not None:
        return str(name).upper()

    return str(status).upper()


def normalize_schedule(schedule):
    if schedule is None:
        return None

    result = {}

    for interval, status in schedule.items():
        result[str(interval)] = get_status_code(status)

    return dict(sorted(result.items()))


def load_previous_state():
    if not STATE_FILE.exists():
        return None

    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        print("Попередній стан не вдалося прочитати.")
        return None


def save_state(snapshot):
    STATE_FILE.write_text(
        json.dumps(
            snapshot,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def format_schedule(title, schedule):
    lines = [title]

    if schedule is None:
        lines.append("Підтверджений графік ще не опубліковано.")
        return lines

    if not schedule:
        lines.append("Дані про часові інтервали відсутні.")
        return lines

    for interval, status in schedule.items():
        description = STATUS_NAMES.get(status, f"Невідомий статус ({status})")
        lines.append(f"{interval} — {description}")

    return lines


def send_telegram_message(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError(
            "Не задано TELEGRAM_BOT_TOKEN або TELEGRAM_CHAT_ID у GitHub Secrets."
        )

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }).encode("utf-8")

    request = Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram відхилив повідомлення: {result}"
            )

        print("Повідомлення успішно надіслано в Telegram.")

    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Помилка Telegram API: HTTP {error.code}. {details}"
        ) from None

    except URLError:
        raise RuntimeError(
            "Не вдалося з'єднатися з Telegram API."
        ) from None


async def get_dtek_schedule():
    print("Отримання браузерної сесії ДТЕК...")

    cookies, csrf_token = await get_cleared_cookies(SCHEDULE_URL)

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
    print(f"Перевіряємо адресу: {CITY}, {STREET}, {HOUSE}")

    async with DtekClient(
        "krem",
        ajax_url=AJAX_URL,
        session=session,
    ) as client:

        address = await client.get_group_by_address(
            city=CITY,
            street=STREET,
            house_number=HOUSE,
        )

        print(f"Знайдена група: {address.group_display_name}")

        if EXPECTED_GROUP not in address.group_display_name:
            raise RuntimeError(
                f"Група не збігається! Очікували {EXPECTED_GROUP}, "
                f"отримали {address.group_display_name}"
            )

        today_schedule = await client.get_today_schedule(
            city=CITY,
            street=STREET,
            house_number=HOUSE,
        )

        tomorrow_schedule = await client.get_tomorrow_schedule(
            city=CITY,
            street=STREET,
            house_number=HOUSE,
        )

        return {
            "group": address.group_display_name,
            "today": normalize_schedule(today_schedule),
            "tomorrow": normalize_schedule(tomorrow_schedule),
        }


async def main():
    now = datetime.now(KYIV_TZ)

    print("=" * 50)
    print("DTEK ELECTRICITY MONITOR")
    print(f"Час перевірки: {now:%d.%m.%Y %H:%M}")
    print("=" * 50)

    data = await get_dtek_schedule()

    if data["today"] is None and data["tomorrow"] is None:
        raise RuntimeError(
            "ДТЕК не повернув підтверджений графік ні на сьогодні, "
            "ні на завтра. Попередній стан не змінюємо."
        )

    snapshot = {
        "date": now.strftime("%Y-%m-%d"),
        "group": data["group"],
        "today": data["today"],
        "tomorrow": data["tomorrow"],
    }

    previous = load_previous_state()

    if previous == snapshot:
        print("Графік не змінився. Повідомлення не надсилаємо.")
        return

    if previous is None:
        message_title = "⚡ Актуальний графік відключень"
    else:
        message_title = "🔔 ГРАФІК ОНОВЛЕНО!"

    message_lines = [
        message_title,
        "",
        "📍 Займище, вул. Центральна, 21",
        f"🔌 {data['group']}",
        f"🕒 Перевірено: {now:%d.%m.%Y %H:%M}",
        "",
    ]

    tomorrow = now.date() + timedelta(days=1)

    message_lines.extend(
        format_schedule(
            f"📅 СЬОГОДНІ — {now:%d.%m.%Y}",
            data["today"],
        )
    )

    message_lines.append("")

    message_lines.extend(
        format_schedule(
            f"📅 ЗАВТРА — {tomorrow:%d.%m.%Y}",
            data["tomorrow"],
        )
    )

    message_lines.extend([
        "",
        "Джерело: ДТЕК Київські регіональні електромережі",
        SCHEDULE_URL,
    ])

    message = "\n".join(message_lines)

    print("\n" + message)

    send_telegram_message(message)

    # Зберігаємо стан тільки після успішного надсилання
    save_state(snapshot)

    print("\nНовий графік збережено.")
    print("Перевірку завершено успішно.")


if __name__ == "__main__":
    asyncio.run(main())
