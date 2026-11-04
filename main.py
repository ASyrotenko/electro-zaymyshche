
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

def append_schedule_section(
    lines,
    title,
    schedule,
    date_string,
):
    lines.append(f"<b>{title}</b>")
    lines.append(f"📅 <b>{format_date(date_string)}</b>")
    lines.append("━━━━")

    if schedule is None:
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

    if not intervals:
        lines.append(
            "ℹ️ <b>Інформація про графік відсутня.</b>"
        )
        return

    for interval in intervals:
        status = interval["status"]

        if status == "YES":
            emoji = "🟢"
            description = "Світло є"

        elif status == "NO":
            emoji = "🔴"
            description = "Світла немає"

        else:
            emoji = "🟡"
            description = "Можливе відключення"

        lines.append(
            f"{emoji} "
            f"<b>{interval['start']} – {interval['end']}</b>"
            f" · {description}"
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
    ]

    append_schedule_section(
        lines=lines,
        title="СЬОГОДНІ",
        schedule=today,
        date_string=today_date,
    )

    lines.extend([
        "━━━━",
        "",
    ])

    append_schedule_section(
        lines=lines,
        title="ЗАВТРА",
        schedule=tomorrow,
        date_string=tomorrow_date,
    )

    lines.append("━━━━")

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
# ДІАГНОСТИКА ЧЕРГ БУДИНКУ
# ============================================================

async def diagnose_house_queues(client):
    print("\n========== ДІАГНОСТИКА ЧЕРГ ==========")

    print(f"Адреса: {CITY}, {STREET}, {HOUSE}")

    if not hasattr(client, "get_home_num"):
        print(
            "Метод get_home_num відсутній у цій версії клієнта."
        )
        print("Діагностику пропускаємо.")
        print("========== КІНЕЦЬ ДІАГНОСТИКИ ==========\n")
        return

    try:
        response = await client.get_home_num(
            city=CITY,
            street=STREET,
        )

        print("Відповідь get_home_num отримана.")

        houses = getattr(response, "houses", None)

        if houses is None:
            print(
                "У відповіді немає атрибута houses."
            )
            print("Повна відповідь:")
            print(object_to_dict(response))
            return

        house = houses.get(HOUSE)

        if house is None:
            print(f"Будинок {HOUSE} не знайдено.")
            print(
                "Доступні будинки:",
                list(houses.keys()),
            )
            return

        group_ids = getattr(house, "group_ids", None)

        print(f"Черги будинку: {group_ids}")
        print(
            "Основна черга:",
            getattr(house, "primary_group", None),
        )
        print(
            "Кілька черг:",
            getattr(house, "is_multi_group", None),
        )
        print(
            "Поточне відключення:",
            getattr(house, "has_current_outage", None),
        )

        if not group_ids:
            print("Список черг порожній.")
            return

        fact = getattr(response, "fact", None)

        if fact is None:
            print("У відповіді немає об'єкта fact.")
            return

        print("\nГРАФІКИ ЗА КОЖНОЮ ЧЕРГОЮ:")

        for group_id in group_ids:
            print(f"\nЧерга {group_id}:")

            try:
                schedule = fact.get_group_today(group_id)
                print(object_to_dict(schedule))

            except Exception as error:
                print(
                    f"Не вдалося отримати графік черги "
                    f"{group_id}: {type(error).__name__}: {error}"
                )

    except Exception as error:
        print("ПОМИЛКА ДІАГНОСТИКИ ЧЕРГ")
        print(f"Тип: {type(error).__name__}")
        print(f"Повідомлення: {error}")

    finally:
        print("\n========== КІНЕЦЬ ДІАГНОСТИКИ ==========\n")


# ============================================================
# ОТРИМАННЯ ГРАФІКА ДТЕК
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

        print("Перевірка адреси ДТЕК...")
        print(f"Адреса: {CITY}, {STREET}, {HOUSE}")

        try:
            address = await client.get_group_by_address(
                city=CITY,
                street=STREET,
                house_number=HOUSE,
            )

            print("Адресу успішно перевірено.")
            print("Результат:", object_to_dict(address))

            # Діагностика черг не повинна блокувати
            # основне отримання графіка.
            await diagnose_house_queues(client)

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
            "ДТЕК не повернув графік ні на сьогодні, ні на завтра."
        )

    previous_state = load_previous_state() or {}

    today_changed = (
        previous_state.get("date") != today_date
        or previous_state.get("today") != today
    )

    tomorrow_changed = (
        previous_state.get("tomorrow_date") != tomorrow_date
        or previous_state.get("tomorrow") != tomorrow
    )

    # Повідомлення надсилається лише тоді,
    # коли змінився графік на сьогодні або завтра.
    if today_changed or tomorrow_changed:
        message = create_telegram_message(
            today=today,
            tomorrow=tomorrow,
            today_date=today_date,
            tomorrow_date=tomorrow_date,
        )

        print("\nПідготовлене повідомлення:")
        print(message)

        send_telegram_message(message)

        print("Спільне повідомлення успішно надіслано.")

    else:
        print(
            "Графік не змінився. "
            "Повідомлення не надсилаємо."
        )

    snapshot = {
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
