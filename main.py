
import asyncio
import html
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from dtek_client.exceptions import DtekDataError

from curl_cffi.requests import AsyncSession
from dtek_client import DtekClient
from dtek_client.browser_auth import get_cleared_cookies


# ============================================================
# НАЛАШТУВАННЯ
# ============================================================

BASE_URL = "https://www.dtek-krem.com.ua"
SCHEDULE_URL = f"{BASE_URL}/ua/shutdowns"
AJAX_URL = f"{BASE_URL}/ua/ajax"

CITY = "с. Займище"
STREET = "вул. Центральна"
HOUSE = "21"

EXPECTED_GROUP = "4.1"

STATE_FILE = Path("state.json")
KYIV_TZ = ZoneInfo("Europe/Kyiv")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


# ============================================================
# СТАТУСИ ЕЛЕКТРОЕНЕРГІЇ
# ============================================================

STATUS_INFO = {
    "YES": {
        "emoji": "🟢",
        "text": "Світло є",
    },
    "NO": {
        "emoji": "🔴",
        "text": "Світла немає",
    },
    "MAYBE": {
        "emoji": "🟡",
        "text": "Можливе відключення",
    },
}


# ============================================================
# ДОПОМІЖНІ ФУНКЦІЇ
# ============================================================

def object_to_dict(value):
    """Перетворює об'єкт бібліотеки на словник, якщо можливо."""

    if value is None:
        return None

    if isinstance(value, dict):
        return value

    if hasattr(value, "model_dump"):
        return value.model_dump()

    if hasattr(value, "dict"):
        return value.dict()

    if hasattr(value, "__dict__"):
        return vars(value)

    return value


def get_status_code(value):
    """Отримує текстовий код статусу."""

    if value is None:
        return None

    if hasattr(value, "value"):
        value = value.value

    if hasattr(value, "name"):
        value = value.name

    return str(value).upper().strip()


def normalize_schedule(schedule):
    """Нормалізує графік до словника година -> статус."""

    schedule = object_to_dict(schedule)

    if schedule is None:
        return None

    if not isinstance(schedule, dict):
        return None

    # Якщо бібліотека повернула об'єкт із вкладеним графіком.
    for key in ("schedule", "hours", "data"):
        nested = schedule.get(key)

        if isinstance(nested, dict):
            schedule = nested
            break

    result = {}

    for hour, value in schedule.items():
        try:
            hour_number = int(hour)
        except (TypeError, ValueError):
            continue

        if not 1 <= hour_number <= 24:
            continue

        status = get_status_code(value)

        if status in ("YES", "NO", "FIRST", "SECOND", "MFIRST", "MSECOND"):
            result[str(hour_number)] = status

    return result or None


def get_schedule_intervals(schedule):
    """
    Перетворює погодинний графік на інтервали.

    YES      — світло є всю годину.
    NO       — світла немає всю годину.
    FIRST    — перша половина без світла, друга зі світлом.
    SECOND   — перша половина зі світлом, друга без світла.
    MFIRST   — можливе відключення першої половини.
    MSECOND  — можливе відключення другої половини.
    """

    schedule = normalize_schedule(schedule)

    if not schedule:
        return []

    segments = []

    for hour in range(1, 25):
        status = schedule.get(str(hour))

        if status is None:
            continue

        start = (hour - 1) * 60

        if status == "YES":
            parts = [
                (start, start + 60, "YES"),
            ]

        elif status == "NO":
            parts = [
                (start, start + 60, "NO"),
            ]

        elif status == "FIRST":
            parts = [
                (start, start + 30, "NO"),
                (start + 30, start + 60, "YES"),
            ]

        elif status == "SECOND":
            parts = [
                (start, start + 30, "YES"),
                (start + 30, start + 60, "NO"),
            ]

        elif status == "MFIRST":
            parts = [
                (start, start + 30, "MAYBE"),
                (start + 30, start + 60, "YES"),
            ]

        elif status == "MSECOND":
            parts = [
                (start, start + 30, "YES"),
                (start + 30, start + 60, "MAYBE"),
            ]

        else:
            continue

        segments.extend(parts)

    # Об'єднуємо сусідні інтервали з однаковим статусом.
    intervals = []

    for start, end, status in segments:
        if (
            intervals
            and intervals[-1]["end"] == start
            and intervals[-1]["status"] == status
        ):
            intervals[-1]["end"] = end
        else:
            intervals.append({
                "start": start,
                "end": end,
                "status": status,
            })

    return intervals


def format_time(minutes):
    """Перетворює хвилини доби на HH:MM."""

    if minutes == 1440:
        return "24:00"

    hours = minutes // 60
    mins = minutes % 60

    return f"{hours:02d}:{mins:02d}"


def format_date(date_string):
    """Форматує дату для повідомлення."""

    try:
        date = datetime.strptime(date_string, "%Y-%m-%d")
        return date.strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return date_string


# ============================================================
# ФОРМУВАННЯ TELEGRAM-ПОВІДОМЛЕННЯ
# ============================================================

def create_telegram_message(schedule, date_string, group, day_name):
    """Створює HTML-повідомлення для Telegram."""

    intervals = get_schedule_intervals(schedule)

    date_formatted = format_date(date_string)

    lines = [
        f"<b>⚡ ГРАФІК ВІДКЛЮЧЕНЬ СВІТЛА — {day_name.upper()}</b>",
        "",
        "📍 Займище, вул. Центральна, 21",
        f"📅 <b>{date_formatted}</b>",
        f"🔢 Черга <b>{html.escape(str(group))}</b>",
        "",
        "━━━━━━━━━━━━━━━━━━",
    ]

    if not intervals:
        lines.extend([
            "",
            "ℹ️ Графік ще не опубліковано.",
        ])
    else:
        for interval in intervals:
            start = format_time(interval["start"])
            end = format_time(interval["end"])

            status = STATUS_INFO[interval["status"]]

            lines.append(
                f'{status["emoji"]} <b>{start} – {end}</b> '
                f'· {status["text"]}'
            )

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "<i>Джерело: ДТЕК Київські регіональні електромережі</i>",
    ])

    return "\n".join(lines)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(message):
    """Надсилає текстове HTML-повідомлення в Telegram."""

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))

        if not result.get("ok"):
            raise RuntimeError(f"Telegram API error: {result}")

        print("Telegram message sent successfully.")

    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Telegram HTTP error {error.code}: {body}"
        ) from error

    except URLError as error:
        raise RuntimeError(
            f"Telegram connection error: {error}"
        ) from error


# ============================================================
# ЗБЕРЕЖЕННЯ ПОПЕРЕДНЬОГО ГРАФІКА
# ============================================================

def load_previous_state():
    if not STATE_FILE.exists():
        return None

    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return None


def save_state(state):
    with STATE_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# ОТРИМАННЯ ГРАФІКА ДТЕК
# ============================================================

async def get_dtek_schedule():

    print("Отримання доступу до сайту ДТЕК...")

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

        try:
            address = await client.get_group_by_address(
                city=CITY,
                street=STREET,
                house_number=HOUSE,
            )

            print(f"Адреса: {CITY}, вул. {STREET}, {HOUSE}")
            print(f"Інформація про адресу: {address}")

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

            return today_schedule, tomorrow_schedule

        except DtekDataError as error:
            print("ПОМИЛКА ДТЕК AJAX")
            print(f"Тип помилки: {type(error).__name__}")
            print(f"Повідомлення: {error}")
            print(f"Відповідь сервера: {getattr(error, 'raw', None)}")
            raise


# ============================================================
# ОСНОВНА ЛОГІКА
# ============================================================



async def main():

    now = datetime.now(KYIV_TZ)

    today_date = now.strftime("%Y-%m-%d")

    from datetime import timedelta
    tomorrow_date = (
        now + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    print("=" * 50)
    print("Перевірка графіка ДТЕК")
    print(f"Дата: {today_date}")
    print(f"Час: {now.strftime('%H:%M:%S')}")
    print("=" * 50)

    today_raw, tomorrow_raw = await get_dtek_schedule()

    today = normalize_schedule(today_raw)
    tomorrow = normalize_schedule(tomorrow_raw)

    print("Графік на сьогодні:")
    print(json.dumps(today, ensure_ascii=False, indent=2))

    print("Графік на завтра:")
    print(json.dumps(tomorrow, ensure_ascii=False, indent=2))

    if today is None and tomorrow is None:
        raise RuntimeError(
            "ДТЕК не повернув графік ні на сьогодні, ні на завтра."
        )

    group = f"Черга {EXPECTED_GROUP}"

    previous_state = load_previous_state() or {}

    # --------------------------------------------------------
    # ПОРІВНЯННЯ ГРАФІКА НА СЬОГОДНІ
    # --------------------------------------------------------

    today_changed = (
        previous_state.get("date") != today_date
        or previous_state.get("today") != today
        or previous_state.get("group") != group
    )

    # --------------------------------------------------------
    # ПОРІВНЯННЯ ГРАФІКА НА ЗАВТРА
    # --------------------------------------------------------

    tomorrow_changed = (
        previous_state.get("tomorrow_date") != tomorrow_date
        or previous_state.get("tomorrow") != tomorrow
        or previous_state.get("group") != group
    )

    # --------------------------------------------------------
    # ПОВІДОМЛЕННЯ НА СЬОГОДНІ
    # --------------------------------------------------------

    if today is not None and today_changed:

        print("Сьогоднішній графік змінився. Надсилаємо повідомлення.")

        message = create_telegram_message(
            schedule=today,
            date_string=today_date,
            group=group,
            day_name="Сьогодні",
        )

        send_telegram_message(message)

    else:
        print("Сьогоднішній графік не змінився.")

    # --------------------------------------------------------
    # ПОВІДОМЛЕННЯ НА ЗАВТРА
    # --------------------------------------------------------

    if tomorrow is not None:

        if tomorrow_changed:

            print("Завтрашній графік змінився. Надсилаємо повідомлення.")

            message = create_telegram_message(
                schedule=tomorrow,
                date_string=tomorrow_date,
                group=group,
                day_name="Завтра",
            )

            send_telegram_message(message)

        else:
            print("Завтрашній графік не змінився.")

    else:

        print("Графік на завтра відсутній. Надсилаємо повідомлення.")

        message = (
            "<b>⚡ ГРАФІК ВІДКЛЮЧЕНЬ СВІТЛА — ЗАВТРА</b>\n\n"
            "📍 Займище, вул. Центральна, 21\n"
            f"📅 <b>{format_date(tomorrow_date)}</b>\n"
            f"🔢 Черга <b>{html.escape(group)}</b>\n\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "ℹ️ <b>Графік на завтра ще не опубліковано.</b>\n\n"
            "ДТЕК поки не надав інформацію про відключення.\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "<i>Джерело: ДТЕК Київські регіональні електромережі</i>"
        )

        # Надсилаємо під час кожної перевірки.
        send_telegram_message(message)

    # --------------------------------------------------------
    # ЗБЕРЕЖЕННЯ СТАНУ
    # --------------------------------------------------------

    snapshot = {
        "date": today_date,
        "tomorrow_date": tomorrow_date,
        "group": group,
        "today": today,
        "tomorrow": tomorrow,
    }

    save_state(snapshot)

    print("Перевірку завершено успішно.")


if __name__ == "__main__":
    asyncio.run(main())
