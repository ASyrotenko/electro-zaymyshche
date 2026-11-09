import asyncio
import json
import os

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from curl_cffi.requests import AsyncSession
from dtek_client import DtekClient
from dtek_client.browser_auth import get_cleared_cookies
from dtek_client.exceptions import DtekDataError


# ============================================================
# НАЛАШТУВАННЯ
# ============================================================

BASE_URL = "https://www.dtek-krem.com.ua"
SCHEDULE_URL = f"{BASE_URL}/ua/shutdowns"
AJAX_URL = f"{BASE_URL}/ua/ajax"

CITY = "с. Займище"
STREET = "вул. Центральна"
HOUSE = "21"

# Використовуємо виключно цю чергу.
TARGET_GROUP_ID = "GPV4.1"
TARGET_QUEUE_DISPLAY = "4.1"

STATE_FILE = Path("state.json")
KYIV_TZ = ZoneInfo("Europe/Kyiv")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


# ============================================================
# ДОПОМІЖНІ ФУНКЦІЇ
# ============================================================

def object_to_dict(value):
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
    if value is None:
        return None

    if isinstance(value, dict):
        value = (
            value.get("status")
            or value.get("value")
            or value.get("code")
        )

    if hasattr(value, "value"):
        value = value.value
    elif hasattr(value, "name"):
        value = value.name

    return str(value).upper().strip()


def normalize_schedule(raw_schedule):
    if raw_schedule is None:
        return None

    schedule = object_to_dict(raw_schedule)

    if not isinstance(schedule, dict):
        raise ValueError(
            f"Невідомий формат графіка ДТЕК: {schedule}"
        )

    for key in ("schedule", "data", "hours"):
        nested = schedule.get(key)

        if isinstance(nested, dict):
            schedule = nested
            break

    normalized = {}

    for hour, status in schedule.items():
        try:
            hour_number = int(hour)
        except (TypeError, ValueError):
            continue

        if not 1 <= hour_number <= 24:
            continue

        status_code = get_status_code(status)

        if status_code not in (
            "YES",
            "NO",
            "FIRST",
            "SECOND",
            "MFIRST",
            "MSECOND",
        ):
            continue

        normalized[str(hour_number)] = status_code

    if not normalized:
        return None

    return dict(
        sorted(
            normalized.items(),
            key=lambda item: int(item[0])
        )
    )


def format_time(minutes):
    if minutes == 1440:
        return "24:00"

    hours = minutes // 60
    remaining_minutes = minutes % 60

    return f"{hours:02d}:{remaining_minutes:02d}"


def format_date(date_string):
    return datetime.strptime(
        date_string,
        "%Y-%m-%d"
    ).strftime("%d.%m.%Y")


# ============================================================
# ПЕРЕТВОРЕННЯ ГРАФІКА НА ЧАСОВІ ІНТЕРВАЛИ
# ============================================================

def get_schedule_intervals(schedule):
    if schedule is None:
        return []

    half_hour_statuses = []

    for hour in range(1, 25):
        status = schedule.get(str(hour))

        if status is None:
            continue

        start_minutes = (hour - 1) * 60

        if status == "YES":
            statuses = ["YES", "YES"]

        elif status == "NO":
            statuses = ["NO", "NO"]

        elif status == "FIRST":
            statuses = ["NO", "YES"]

        elif status == "SECOND":
            statuses = ["YES", "NO"]

        elif status == "MFIRST":
            statuses = ["MAYBE", "YES"]

        elif status == "MSECOND":
            statuses = ["YES", "MAYBE"]

        else:
            continue

        for half_index, half_status in enumerate(statuses):
            half_hour_statuses.append({
                "start": start_minutes + half_index * 30,
                "end": start_minutes + (half_index + 1) * 30,
                "status": half_status,
            })

    if not half_hour_statuses:
        return []

    merged = []

    for item in half_hour_statuses:
        if (
            merged
            and merged[-1]["status"] == item["status"]
            and merged[-1]["end"] == item["start"]
        ):
            merged[-1]["end"] = item["end"]

        else:
            merged.append(item.copy())

    return [
        {
            "start": format_time(item["start"]),
            "end": format_time(item["end"]),
            "status": item["status"],
        }
        for item in merged
    ]


# ============================================================
# ФОРМУВАННЯ ПОВІДОМЛЕННЯ
# ============================================================

def format_duration(minutes, always_show_minutes=False):
    hours = minutes // 60
    remaining_minutes = minutes % 60

    if always_show_minutes or remaining_minutes:
        return f"{hours} год {remaining_minutes:02d} хв"

    return f"{hours} год"


def get_outage_duration(intervals):
    """
    Рахуємо лише гарантовані відключення (NO).
    Можливі відключення (MAYBE) показуємо окремо,
    але не додаємо до загального часу без світла.
    """
    total_minutes = 0

    for interval in intervals:
        if interval["status"] != "NO":
            continue

        start_hour, start_minute = map(
            int, interval["start"].split(":")
        )
        end_hour, end_minute = map(
            int, interval["end"].split(":")
        )

        start = start_hour * 60 + start_minute
        end = (
            1440
            if interval["end"] == "24:00"
            else end_hour * 60 + end_minute
        )

        total_minutes += end - start

    return total_minutes


def append_schedule_section(
    lines,
    title,
    schedule,
    date_string,
):
    if schedule is None:
        lines.append(f"<b>{title}</b>")
        lines.append(f"📅 <b>{format_date(date_string)}</b>")
        lines.append("━━━━━━━━━━━━")

        if title == "ЗАВТРА":
            lines.append(
                "ℹ️ <b>Графік на завтра ще не опубліковано.</b>"
            )
        else:
            lines.append(
                "ℹ️ <b>Графік на сьогодні недоступний.</b>"
            )

        return

    intervals = get_schedule_intervals(schedule)

    total_minutes = get_outage_duration(intervals)

    total_duration = format_duration(
        total_minutes,
        always_show_minutes=True,
    )

    lines.append(f"<b>{title} (~{total_duration})</b>")
    lines.append(f"📅 <b>{format_date(date_string)}</b>")
    lines.append("━━━━━━━━━━━━")

    # Якщо немає ні гарантованих, ні можливих відключень,
    # показуємо коротке повідомлення замість 24 годин зелених інтервалів.
    has_outages = any(
        interval["status"] in ("NO", "MAYBE")
        for interval in intervals
    )

    if not has_outages:
        lines.append("💡 <b>Відключення не заплановані</b>")
        return

    # Якщо відключення є, показуємо повну шкалу:
    # і світло є, і світла немає.
    for interval in intervals:
        status = interval["status"]

        start_hour, start_minute = map(
            int, interval["start"].split(":")
        )
        end_hour, end_minute = map(
            int, interval["end"].split(":")
        )

        start = start_hour * 60 + start_minute

        end = (
            1440
            if interval["end"] == "24:00"
            else end_hour * 60 + end_minute
        )

        duration = format_duration(end - start)

        if status == "YES":
            emoji = "🟢"
        elif status == "NO":
            emoji = "🔴"
        else:  # MAYBE
            emoji = "🟡"

        lines.append(
            f"{emoji} "
            f"<b>{interval['start']} – {interval['end']}</b>"
            f" · (~{duration})"
        )


def create_telegram_message(
    today,
    tomorrow,
    today_date,
    tomorrow_date,
):
    lines = [
        "<b>⚡ ГРАФІК ВІДКЛЮЧЕНЬ СВІТЛА</b>",
        "",
        f"<b>ЧЕРГА - {TARGET_QUEUE_DISPLAY}</b>",
        "",
    ]

    append_schedule_section(
        lines=lines,
        title="СЬОГОДНІ",
        schedule=today,
        date_string=today_date,
    )

    lines.extend([
        "━━━━━━━━━━━━",
        "",
    ])

    append_schedule_section(
        lines=lines,
        title="ЗАВТРА",
        schedule=tomorrow,
        date_string=tomorrow_date,
    )

    lines.append("━━━━━━━━━━━━")

    return "\n".join(lines)


# ============================================================
# ВІДПРАВЛЕННЯ ПОВІДОМЛЕННЯ TELEGRAM
# ============================================================

def send_telegram_message(message):
    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    request = Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(
                response.read().decode("utf-8")
            )

    except HTTPError as error:
        response_text = error.read().decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"Помилка Telegram HTTP {error.code}: {response_text}"
        )

    except URLError as error:
        raise RuntimeError(
            f"Не вдалося підключитися до Telegram: {error}"
        )

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API повернув помилку: {result}"
        )

    print("Повідомлення успішно надіслано в Telegram.")


# ============================================================
# ЗБЕРЕЖЕННЯ ПОПЕРЕДНЬОГО СТАНУ
# ============================================================

def load_previous_state():
    if not STATE_FILE.exists():
        return None

    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            return json.load(file)

    except (json.JSONDecodeError, OSError):
        return None


def save_state(snapshot):
    with STATE_FILE.open("w", encoding="utf-8") as file:
        json.dump(
            snapshot,
            file,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# ОТРИМАННЯ ГРАФІКА ДТЕК САМЕ ДЛЯ GPV4.1
# ============================================================

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

        print("Отримання інформації про будинок...")

        try:
            response = await client.get_home_num(
                city=CITY,
                street=STREET,
            )

            house = response.houses.get(HOUSE)

            if house is None:
                raise RuntimeError(
                    f"Будинок {HOUSE} не знайдено у відповіді ДТЕК."
                )

            group_ids = house.group_ids or []

            print(f"Черги будинку: {group_ids}")
            print(f"Обрана черга: {TARGET_GROUP_ID}")

            if TARGET_GROUP_ID not in group_ids:
                raise RuntimeError(
                    f"Черга {TARGET_GROUP_ID} відсутня "
                    f"у списку черг будинку: {group_ids}"
                )

            if response.fact is None:
                raise RuntimeError(
                    "ДТЕК не повернув фактичний графік (fact)."
                )

            fact = response.fact

            print("Отримання графіка на сьогодні...")

            today = fact.get_group_today(TARGET_GROUP_ID)

            print("Отримання графіка на завтра...")

            tomorrow_timestamp = fact.today_ts + 86400

            tomorrow = fact.get_group_day(
                tomorrow_timestamp,
                TARGET_GROUP_ID,
            )

            print(
                f"Графік на сьогодні для {TARGET_GROUP_ID}: "
                f"{object_to_dict(today)}"
            )

            print(
                f"Графік на завтра для {TARGET_GROUP_ID}: "
                f"{object_to_dict(tomorrow)}"
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


# ============================================================
# ГОЛОВНА ФУНКЦІЯ
# ============================================================

async def main():
    now = datetime.now(KYIV_TZ)

    # Не перевіряємо графік з 23:00 до 08:00.
    if now.hour >= 23 or now.hour < 8:
        print(
            f"Зараз {now.strftime('%H:%M:%S')} за Києвом. "
            "Нічний період (23:00–08:00). "
            "Перевірку пропускаємо."
        )
        return

    today_date = now.strftime("%Y-%m-%d")

    tomorrow_date = (
        now + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    print("=" * 50)
    print("ПЕРЕВІРКА ГРАФІКА ДТЕК")
    print(f"Адреса: {CITY}, {STREET}, {HOUSE}")
    print(f"Черга: {TARGET_GROUP_ID}")
    print(f"Дата: {today_date}")
    print(f"Час: {now.strftime('%H:%M:%S')}")
    print("=" * 50)

    today_raw, tomorrow_raw = await get_dtek_schedule()

    today = normalize_schedule(today_raw)
    tomorrow = normalize_schedule(tomorrow_raw)

    print("\nГрафік на сьогодні:")
    print(
        json.dumps(
            today,
            ensure_ascii=False,
            indent=2,
        )
    )

    print("\nГрафік на завтра:")
    print(
        json.dumps(
            tomorrow,
            ensure_ascii=False,
            indent=2,
        )
    )

    if today is None and tomorrow is None:
        raise RuntimeError(
            f"ДТЕК не повернув графік для {TARGET_GROUP_ID} "
            "ні на сьогодні, ні на завтра."
        )

    previous_state = load_previous_state() or {}

    # Порівнюємо також номер черги.
    # Після переходу зі старої версії на GPV4.1
    # це спричинить одне оновлення повідомлення.
    group_changed = (
        previous_state.get("group") != TARGET_GROUP_ID
    )

    today_changed = (
        previous_state.get("date") != today_date
        or previous_state.get("today") != today
    )

    tomorrow_changed = (
        previous_state.get("tomorrow_date") != tomorrow_date
        or previous_state.get("tomorrow") != tomorrow
    )

    if group_changed or today_changed or tomorrow_changed:
        message = create_telegram_message(
            today=today,
            tomorrow=tomorrow,
            today_date=today_date,
            tomorrow_date=tomorrow_date,
        )

        print("\nПідготовлене повідомлення:")
        print(message)

        send_telegram_message(message)

        print("Повідомлення успішно надіслано.")

    else:
        print(
            "Графік не змінився. "
            "Повідомлення не надсилаємо."
        )

    snapshot = {
        "group": TARGET_GROUP_ID,
        "date": today_date,
        "tomorrow_date": tomorrow_date,
        "today": today,
        "tomorrow": tomorrow,
    }

    save_state(snapshot)

    print("Стан графіка збережено.")
    print("Перевірку завершено успішно.")


# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":
    asyncio.run(main())
