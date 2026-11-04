
import asyncio
import json
import os
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

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


# ============================================================
# СТАТУСИ ДТЕК
# ============================================================

STATUS_NAMES = {
    "YES": "Світло є",
    "NO": "Світла немає",
    "MAYBE": "Можливе відключення",
    "UNKNOWN": "Статус невідомий",
}


STATUS_COLORS = {
    "YES": {
        "background": "#183D2C",
        "border": "#35C978",
        "text": "#BDF5D2",
        "label": "Світло є",
    },
    "NO": {
        "background": "#482323",
        "border": "#EF6262",
        "text": "#FFC5C5",
        "label": "Світла немає",
    },
    "MAYBE": {
        "background": "#493B1E",
        "border": "#E5B94B",
        "text": "#FFE6A3",
        "label": "Можливе відключення",
    },
}


# ============================================================
# НОРМАЛІЗАЦІЯ ГРАФІКА
# ============================================================

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

    return dict(
        sorted(
            result.items(),
            key=lambda item: int(item[0])
        )
    )


def get_schedule_intervals(schedule):
    """
    Перетворює погодинний графік на об'єднані часові інтервали.

    NO       — вся година без світла.
    YES      — вся година зі світлом.
    FIRST    — перші 30 хвилин без світла.
    SECOND   — останні 30 хвилин без світла.
    MFIRST   — можливе відключення перші 30 хвилин.
    MSECOND  — можливе відключення останні 30 хвилин.
    """

    if not schedule:
        return []

    half_hours = []

    for hour, status in schedule.items():

        try:
            hour_number = int(hour)
        except (TypeError, ValueError):
            continue

        if not 1 <= hour_number <= 24:
            continue

        start = (hour_number - 1) * 2

        if status == "FIRST":
            statuses = ["NO", "YES"]

        elif status == "SECOND":
            statuses = ["YES", "NO"]

        elif status == "MFIRST":
            statuses = ["MAYBE", "YES"]

        elif status == "MSECOND":
            statuses = ["YES", "MAYBE"]

        else:
            statuses = [status, status]

        half_hours.append((start, statuses[0]))
        half_hours.append((start + 1, statuses[1]))

    half_hours.sort(key=lambda item: item[0])

    if not half_hours:
        return []

    intervals = []

    start = half_hours[0][0]
    previous = start
    previous_status = half_hours[0][1]

    for position, status in half_hours[1:]:

        if position == previous + 1 and status == previous_status:
            previous = position
            continue

        intervals.append(
            (start, previous + 1, previous_status)
        )

        start = position
        previous = position
        previous_status = status

    intervals.append(
        (start, previous + 1, previous_status)
    )

    return intervals


def format_time(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# ============================================================
# СТВОРЕННЯ ЗОБРАЖЕННЯ
# ============================================================

def render_schedule_image(data, today, section="today"):
    """
    Створює PNG-зображення графіка.

    section:
        today    — графік на сьогодні;
        tomorrow — графік на завтра.
    """

    WIDTH = 1100

    MARGIN = 45
    ROW_HEIGHT = 76
    ROW_GAP = 13

    BACKGROUND = "#101820"
    TEXT_COLOR = "#FFFFFF"

    regular_font_path = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    )

    bold_font_path = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    )

    title_font = ImageFont.truetype(
        bold_font_path, 39
    )

    day_font = ImageFont.truetype(
        bold_font_path, 29
    )

    time_font = ImageFont.truetype(
        bold_font_path, 27
    )

    status_font = ImageFont.truetype(
        regular_font_path, 25
    )

    small_font = ImageFont.truetype(
        regular_font_path, 20
    )

    if section == "today":
        section_title = "СЬОГОДНІ"
        section_date = today
        schedule = data["today"]
    else:
        section_title = "ЗАВТРА"
        section_date = today + timedelta(days=1)
        schedule = data["tomorrow"]

    intervals = get_schedule_intervals(schedule)

    # Розрахунок висоти зображення
    if schedule is None:
        rows_count = 1
    else:
        rows_count = max(1, len(intervals))

    height = (
        250
        + 75
        + rows_count * (ROW_HEIGHT + ROW_GAP)
        + 100
    )

    image = Image.new(
        "RGB",
        (WIDTH, height),
        BACKGROUND,
    )

    draw = ImageDraw.Draw(image)

    y = 35

    # Заголовок
    draw.text(
        (MARGIN, y),
        "ГРАФІК ВІДКЛЮЧЕНЬ",
        font=title_font,
        fill=TEXT_COLOR,
    )

    y += 65

    # Адреса
    draw.text(
        (MARGIN, y),
        "Займище · вул. Центральна, 21",
        font=small_font,
        fill="#B5C2CE",
    )

    y += 35

    # Черга
    draw.text(
        (MARGIN, y),
        f"{data['group']}  |  {section_date:%d.%m.%Y}",
        font=small_font,
        fill="#B5C2CE",
    )

    y += 65

    # Назва дня
    draw.text(
        (MARGIN, y),
        f"{section_title} — {section_date:%d.%m.%Y}",
        font=day_font,
        fill=TEXT_COLOR,
    )

    y += 55

    if schedule is None:

        draw.rounded_rectangle(
            (
                MARGIN,
                y,
                WIDTH - MARGIN,
                y + 65,
            ),
            radius=16,
            fill="#29333D",
            outline="#64717E",
            width=2,
        )

        draw.text(
            (MARGIN + 22, y + 17),
            "Графік ще не опубліковано",
            font=status_font,
            fill="#D2D9E0",
        )

    elif not intervals:

        draw.text(
            (MARGIN, y),
            "Дані про часові інтервали відсутні",
            font=status_font,
            fill="#D2D9E0",
        )

    else:

        for start, end, status in intervals:

            color = STATUS_COLORS.get(
                status,
                {
                    "background": "#29333D",
                    "border": "#64717E",
                    "text": "#FFFFFF",
                    "label": f"Невідомий статус: {status}",
                },
            )

            # Кольоровий блок
            draw.rounded_rectangle(
                (
                    MARGIN,
                    y,
                    WIDTH - MARGIN,
                    y + ROW_HEIGHT,
                ),
                radius=17,
                fill=color["background"],
                outline=color["border"],
                width=3,
            )

            # Час
            start_text = format_time(start * 30)
            end_text = format_time(end * 30)

            time_text = f"{start_text} – {end_text}"

            draw.text(
                (MARGIN + 25, y + 21),
                time_text,
                font=time_font,
                fill=TEXT_COLOR,
            )

            # Статус
            status_text = color["label"]

            bbox = draw.textbbox(
                (0, 0),
                status_text,
                font=status_font,
            )

            status_width = bbox[2] - bbox[0]

            draw.text(
                (
                    WIDTH - MARGIN - 25 - status_width,
                    y + 23,
                ),
                status_text,
                font=status_font,
                fill=color["text"],
            )

            y += ROW_HEIGHT + ROW_GAP

    # Джерело
    draw.text(
        (MARGIN, height - 45),
        "Джерело: ДТЕК Київські регіональні електромережі",
        font=small_font,
        fill="#8D9AA7",
    )

    output = BytesIO()

    image.save(
        output,
        format="PNG",
        optimize=True,
    )

    return output.getvalue()


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_photo(photo_bytes, caption):

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError(
            "Не задано TELEGRAM_BOT_TOKEN або TELEGRAM_CHAT_ID."
        )

    boundary = "----DTEKMonitorBoundary"

    body = bytearray()

    def add_field(name, value):

        body.extend(
            f"--{boundary}\r\n".encode()
        )

        body.extend(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        )

        body.extend(
            str(value).encode()
        )

        body.extend(b"\r\n")

    add_field("chat_id", chat_id)
    add_field("caption", caption)

    body.extend(
        f"--{boundary}\r\n".encode()
    )

    body.extend(
        b'Content-Disposition: form-data; name="photo"; filename="schedule.png"\r\n'
    )

    body.extend(
        b"Content-Type: image/png\r\n\r\n"
    )

    body.extend(photo_bytes)
    body.extend(b"\r\n")

    body.extend(
        f"--{boundary}--\r\n".encode()
    )

    request = Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=bytes(body),
        headers={
            "Content-Type": (
                f"multipart/form-data; boundary={boundary}"
            ),
        },
        method="POST",
    )

    try:

        with urlopen(request, timeout=30) as response:

            result = json.loads(
                response.read().decode("utf-8")
            )

        if not result.get("ok"):
            raise RuntimeError(
                f"Telegram відхилив зображення: {result}"
            )

        print(
            "Зображення графіка успішно надіслано в Telegram."
        )

    except HTTPError as error:

        details = error.read().decode(
            "utf-8",
            errors="replace",
        )

        raise RuntimeError(
            f"Помилка Telegram API: HTTP {error.code}. {details}"
        ) from None

    except URLError:

        raise RuntimeError(
            "Не вдалося з'єднатися з Telegram API."
        ) from None


# ============================================================
# ЗБЕРЕЖЕННЯ ПОПЕРЕДНЬОГО ГРАФІКА
# ============================================================

def load_previous_state():

    if not STATE_FILE.exists():
        return None

    try:

        return json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

    except (json.JSONDecodeError, OSError):

        print(
            "Попередній стан не вдалося прочитати."
        )

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


# ============================================================
# ОТРИМАННЯ ГРАФІКА ДТЕК
# ============================================================

async def get_dtek_schedule():

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

        "Accept": (
            "application/json, text/javascript, */*; q=0.01"
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

    print("Браузерну сесію отримано.")

    print(
        f"Перевіряємо адресу: {CITY}, {STREET}, {HOUSE}"
    )

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

        print(
            f"Знайдена група: {address.group_display_name}"
        )

        if EXPECTED_GROUP not in address.group_display_name:

            raise RuntimeError(
                f"Група не збігається! "
                f"Очікували {EXPECTED_GROUP}, "
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


# ============================================================
# ГОЛОВНА ФУНКЦІЯ
# ============================================================

async def main():

    now = datetime.now(KYIV_TZ)

    print("=" * 55)
    print("DTEK ELECTRICITY MONITOR")
    print(f"Час перевірки: {now:%d.%m.%Y %H:%M}")
    print("=" * 55)

    data = await get_dtek_schedule()

    print("\nОтриманий графік:")

    print(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        )
    )

    if data["today"] is None and data["tomorrow"] is None:

        raise RuntimeError(
            "ДТЕК не повернув графік ні на сьогодні, "
            "ні на завтра. Попередній стан не змінюємо."
        )

    snapshot = {
        "date": now.strftime("%Y-%m-%d"),
        "group": data["group"],
        "today": data["today"],
        "tomorrow": data["tomorrow"],
    }

    previous = load_previous_state()

    # Якщо графік не змінився — нічого не надсилаємо
    if previous == snapshot:

        print(
            "\nГрафік не змінився. "
            "Повідомлення не надсилаємо."
        )

        return

    if previous is None:

        print(
            "\nПерший запуск. Надсилаємо актуальний графік."
        )

    else:

        print(
            "\nВиявлено зміну графіка. "
            "Надсилаємо оновлення."
        )

    # Підпис до зображення
    caption = (
        f"⚡ Графік відключень\n"
        f"📍 Займище, вул. Центральна, 21\n"
        f"🔌 {data['group']}\n"
        f"🕒 Перевірено: {now:%d.%m.%Y %H:%M}\n\n"
        f"Джерело: ДТЕК Київські регіональні електромережі\n"
        f"{SCHEDULE_URL}"
    )

    # Створюємо окреме зображення на сьогодні
    today_image = render_schedule_image(
        data=data,
        today=now.date(),
        section="today",
    )

    send_telegram_photo(
        photo_bytes=today_image,
        caption=caption,
    )

    # Якщо є графік на завтра — надсилаємо друге зображення
    if data["tomorrow"] is not None:

        tomorrow_image = render_schedule_image(
            data=data,
            today=now.date(),
            section="tomorrow",
        )

        send_telegram_photo(
            photo_bytes=tomorrow_image,
            caption=(
                f"⚡ Графік відключень на завтра\n"
                f"📍 Займище, вул. Центральна, 21\n"
                f"🔌 {data['group']}\n"
                f"🕒 Перевірено: {now:%d.%m.%Y %H:%M}"
            ),
        )

    # Зберігаємо стан тільки після успішного надсилання
    save_state(snapshot)

    print("\nНовий графік збережено.")
    print("Перевірку завершено успішно.")


if __name__ == "__main__":

    asyncio.run(main())
