import imaplib
import email
from email.header import decode_header
import time
import pandas as pd
import requests
import warnings
import asyncio
import threading
import io
import logging
from datetime import datetime, timedelta
from clickhouse_driver import Client
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

# Настраиваем логирование: скрываем шум от бота при обрыве сети
logging.basicConfig(level=logging.ERROR)
logger_aiogram = logging.getLogger("aiogram")
logger_aiogram.setLevel(logging.CRITICAL)
logger_aiohttp = logging.getLogger("aiohttp")
logger_aiohttp.setLevel(logging.CRITICAL)

warnings.filterwarnings("ignore")

# ================= НАСТРОЙКИ =================
EMAIL_ACCOUNT = "a.vladimirov@limehd.tv"
EMAIL_PASSWORD = "gjtwejzhotmyalsx"
IMAP_SERVER = "imap.yandex.ru"

SUBJECT_KEYWORD = "BetweenX Report"

CH_CONFIG = {
    'host': '172.19.95.127',
    'port': 9000,
    'user': 'sandbox_analytic',
    'password': 'fy7$ndDOjp.r',
    'database': 'sandbox'
}
TABLE_NAME = 'vtochku_between'

TELEGRAM_TOKEN = "8780548561:AAFAZFWuy4RIjN1oNTfND6imRcFmbXoYSdI"
CHAT_ID = 5106855055

START_HOUR = 11
START_MINUTE = 40
CHECK_INTERVAL = 300  # 5 минут
SCAN_LAST_COUNT = 20
# ===============================================

bot_instance = None


def decode_mime_words(s: str) -> str:
    if not s:
        return ""
    decoded = decode_header(s)
    return ''.join(
        word.decode(encoding or 'utf8') if isinstance(word, bytes) else word
        for word, encoding in decoded
    )


def format_number(num: float | int) -> str:
    try:
        return f"{int(num):,}".replace(",", " ")
    except (ValueError, TypeError):
        return str(num)


def send_telegram_message(text: str, is_alert: bool = False) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    if is_alert:
        payload["text"] = f"🚨 <b>ALERT:</b>\n{text}"
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception:
        return False


# --- Логика Бота ---

async def cmd_start(message: types.Message):
    kb = [[KeyboardButton(text="🔄 Перепроверить почту")]]
    keyboard = ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    await message.answer(
        "👋 Привет! Я бот-загрузчик.\nСкрипт работает в фоновом режиме.\nЖми кнопку для проверки!",
        reply_markup=keyboard
    )


async def check_manual(message: types.Message):
    await message.answer("⏳ Запускаю проверку почты...")
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    success = await loop.run_in_executor(None, run_single_check)

    if success:
        await message.answer("✅ Проверка завершена! Новые данные загружены.")
    else:
        await message.answer("ℹ️ Новых писем не найдено или отчет уже есть в базе.")


async def bot_polling_loop():
    global bot_instance
    bot_instance = Bot(token=TELEGRAM_TOKEN)
    dp = Dispatcher()

    dp.message.register(cmd_start, Command("start"))
    dp.message.register(check_manual, lambda msg: msg.text == "🔄 Перепроверить почту")

    print("🤖 Бот запущен и ждет команд (24/7)...")
    try:
        await dp.start_polling(bot_instance, allowed_updates=dp.resolve_used_update_types())
    except Exception as e:
        print(f"⚠️ Бот остановлен: {e}")
    finally:
        await bot_instance.session.close()


def run_bot_thread():
    while True:
        try:
            asyncio.run(bot_polling_loop())
        except Exception as e:
            print(f"⚠️ Ошибка бота, перезапуск через 10 сек... ({e})")
            time.sleep(10)


# --- Основная логика проверки ---

def run_single_check() -> bool:
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"[{timestamp}] 🔍 Запуск проверки почты...")

    mail = None
    loaded_count = 0
    retry_count = 0

    while retry_count < 2:
        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER)
            mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
            mail.select("inbox")
            time.sleep(2)

            search_query = f'(SUBJECT "{SUBJECT_KEYWORD}")'
            status, messages = mail.search(None, search_query)

            if status != "OK":
                if retry_count == 0:
                    retry_count += 1
                    if mail:
                        try:
                            mail.close()
                            mail.logout()
                        except Exception:
                            pass
                    continue
                else:
                    raise Exception(f"IMAP Search Error: {status}")

            if not messages or not messages[0]:
                all_email_ids = []
            else:
                all_email_ids = messages[0].split()

            if not all_email_ids:
                print("   ℹ️ Письма не найдены.")
                return False

            recent_ids = all_email_ids[-SCAN_LAST_COUNT:]
            recent_ids.reverse()
            print(f"   📬 Найдено: {len(all_email_ids)}. Проверяем последние {len(recent_ids)}...")

            for email_id in recent_ids:
                _, msg_data = mail.fetch(email_id, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                subject = decode_mime_words(msg.get("Subject", ""))

                if SUBJECT_KEYWORD not in subject:
                    continue

                print(f"\n   📨 Обработка: {subject}")
                file_found = False
                load_success = False

                for part in msg.walk():
                    if "attachment" in str(part.get("Content-Disposition")):
                        filename = decode_mime_words(part.get_filename())
                        if filename and filename.lower().endswith('.csv'):
                            file_found = True
                            print(f"      📎 Файл в памяти: {filename}")

                            file_data = part.get_payload(decode=True)
                            csv_buffer = io.BytesIO(file_data)

                            try:
                                try:
                                    df = pd.read_csv(csv_buffer, encoding='utf-8')
                                except UnicodeDecodeError:
                                    csv_buffer.seek(0)
                                    df = pd.read_csv(csv_buffer, encoding='cp1251')

                                if df.empty:
                                    raise ValueError("Файл пуст (нет строк данных)")

                                required_cols = ['date', 'hour']
                                missing_cols = [col for col in required_cols if col not in df.columns]

                                if missing_cols:
                                    cols_str = ", ".join([f"'{c}'" for c in missing_cols])
                                    raise ValueError(
                                        f"❌ Нарушена структура файла! Отсутствуют обязательные столбцы: {cols_str}."
                                    )

                                report_date = pd.to_datetime(df['date'].iloc[0]).date()
                                print(f"      📅 Дата отчета: {report_date}")

                                if check_db_for_date(report_date):
                                    print("      ✅ Уже в БД")
                                    load_success = True
                                else:
                                    df_prepared = prepare_data_for_ch(df)
                                    rows, imp, rev, date = insert_to_clickhouse(df_prepared)

                                    if rows > 0:
                                        print(f"      🚀 Загружено {rows} строк!")
                                        loaded_count += 1
                                        load_success = True
                                        msg_text = (
                                            f"📊 <b>Отчет выгружен!</b>\n\n"
                                            f"📅 Дата: <b>{date}</b>\n"
                                            f"📈 Impressions: <b>{format_number(int(imp))}</b>\n"
                                            f"💰 Revenue: <b>${rev:,.2f}</b>"
                                        )
                                        send_telegram_message(msg_text)
                                    else:
                                        raise ValueError("Загрузка вернула 0 строк")

                            except ValueError as ve:
                                err_text = str(ve)
                                print(f"      ❌ Ошибка данных: {err_text}")

                                alert_msg = (
                                    f"📁 Файл: <b>{filename}</b>\n"
                                    f"📩 Тема: {subject}\n\n"
                                    f"⚠️ <b>Ошибка структуры или данных:</b>\n{err_text}"
                                )
                                send_telegram_message(alert_msg, is_alert=True)

                            except Exception as e:
                                err_text = str(e)
                                print(f"      ❌ Критическая ошибка: {err_text}")
                                alert_msg = (
                                    f"📁 Файл: <b>{filename}</b>\n"
                                    f"📩 Тема: {subject}\n\n"
                                    f"🚨 <b>Критическая ошибка обработки:</b>\n{err_text}"
                                )
                                send_telegram_message(alert_msg, is_alert=True)

                            break

                if file_found and load_success:
                    mail.store(email_id, '+FLAGS', '\\Seen')
                elif not file_found:
                    mail.store(email_id, '+FLAGS', '\\Seen')

            print(f"\n--- Итог: загружено {loaded_count} отчетов ---")
            return loaded_count > 0

        except Exception as e:
            err_msg = f"Критическая ошибка цикла: {e}"
            print(f"   ❌ {err_msg}")
            if retry_count >= 2:
                send_telegram_message(err_msg, is_alert=True)
            return False
        finally:
            if mail:
                try:
                    mail.close()
                    mail.logout()
                except Exception:
                    pass
    return False


# --- Вспомогательные функции БД ---

def check_db_for_date(target_date):
    client = None
    try:
        client = Client(**CH_CONFIG)
        query = f"SELECT count() FROM {TABLE_NAME} WHERE event_date = '{target_date}'"
        result = client.execute(query)
        return (result[0][0] > 0) if result else False
    except Exception:
        return False
    finally:
        if client:
            client.disconnect()


def prepare_data_for_ch(df):
    required = ['date', 'hour']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Отсутствуют колонки: {missing}")

    df = df.copy()

    df['event_date'] = pd.to_datetime(df['date']).dt.date
    df['event_hour'] = pd.to_datetime(df['hour']).dt.hour

    numeric_cols = [
        'publisher_id',
        'section_id',
        'bid_responses',
        'responses',
        'impressions',
        'v_firstq',
        'v_midpoint',
        'v_thirdq',
        'v_complete'
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)

    decimal_cols = ['net_payable', 'actual_pub']
    for col in decimal_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)

    if 'cp_bidder_name' in df.columns:
        df['DSP_NAME'] = (
            df['cp_bidder_name']
            .fillna('')
            .astype(str)
            .str.replace('Lime4DSP - ', '', regex=False)
        )
    else:
        df['DSP_NAME'] = ''

    if 'section_name' in df.columns:
        df['PLATFORM'] = (
            df['section_name']
            .fillna('')
            .astype(str)
            .str.strip()
            .str.lower()
            .map({
                "на мобильных телефонах vpaid wrapper": "Mobile",
                "на смартах vpaid wrapper": "CTV"
            })
            .fillna('')
        )
    else:
        df['PLATFORM'] = ''

    bid_responses = pd.to_numeric(df.get('bid_responses', 0), errors='coerce').fillna(0)
    responses = pd.to_numeric(df.get('responses', 0), errors='coerce').fillna(0)
    impressions = pd.to_numeric(df.get('impressions', 0), errors='coerce').fillna(0)
    net_payable = pd.to_numeric(df.get('net_payable', 0), errors='coerce').fillna(0)

    df['FILL_RATE'] = ((responses / bid_responses.replace(0, pd.NA)) * 100).fillna(0).round(2)
    df['SHOW_RATE'] = ((impressions / responses.replace(0, pd.NA)) * 100).fillna(0).round(2)
    df['CPM'] = ((net_payable / impressions.replace(0, pd.NA)) * 1000).fillna(0).round(2)
    df['INVENTORY_TYPE'] = 'DSP'

    columns_map = {
        'event_date': 'event_date',
        'event_hour': 'event_hour',
        'publisher_id': 'publisher_id',
        'section_name': 'section_name',
        'section_id': 'section_id',
        'cp_bidder_name': 'cp_bidder_name',
        'bid_responses': 'bid_responses',
        'responses': 'responses',
        'impressions': 'impressions',
        'net_payable': 'net_payable',
        'actual_pub': 'actual_pub',
        'v_firstq': 'v_firstq',
        'v_midpoint': 'v_midpoint',
        'v_thirdq': 'v_thirdq',
        'v_complete': 'v_complete',
        'DSP_NAME': 'DSP_NAME',
        'PLATFORM': 'PLATFORM',
        'FILL_RATE': 'FILL_RATE',
        'SHOW_RATE': 'SHOW_RATE',
        'CPM': 'CPM',
        'INVENTORY_TYPE': 'INVENTORY_TYPE',
    }

    available_cols = [k for k in columns_map.keys() if k in df.columns]
    final_df = df[available_cols].rename(columns={k: columns_map[k] for k in available_cols})

    if 'impressions' not in final_df.columns or 'event_date' not in final_df.columns:
        raise ValueError("Нет ключевых колонок после маппинга")

    final_df['inserted_at'] = datetime.now()
    return final_df


def insert_to_clickhouse(df):
    client = None
    try:
        client = Client(**CH_CONFIG)
        data = df.to_dict('records')
        if not data:
            return 0, 0, 0, None

        cols = list(df.columns)
        query = f"INSERT INTO {TABLE_NAME} ({', '.join(cols)}) VALUES"
        client.execute(query, data)

        return len(data), int(df['impressions'].sum()), float(df['net_payable'].sum()), df['event_date'].iloc[0]
    except Exception as e:
        raise e
    finally:
        if client:
            client.disconnect()


def wait_until_start_time():
    now = datetime.now()
    target = now.replace(hour=START_HOUR, minute=START_MINUTE, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    sleep_time = (target - now).total_seconds()
    if sleep_time < 0 or sleep_time > 90000:
        sleep_time = 86400
    print(f"⏳ Сон до {target.strftime('%H:%M')} ({sleep_time / 60:.1f} мин.)")
    time.sleep(sleep_time)


# --- Запуск ---

def main():
    print("=" * 60)
    print("🤖 Умный загрузчик v9.4 (Smart Error Reporting)")
    print(f"🕒 Авто-старт: {START_HOUR:02d}:{START_MINUTE:02d}")
    print("💡 Бот работает 24/7")
    print("=" * 60)

    bot_t = threading.Thread(target=run_bot_thread, daemon=True)
    bot_t.start()
    time.sleep(2)

    while True:
        try:
            wait_until_start_time()

            print(f"\n☀️ Проснулся по расписанию! ({datetime.now().strftime('%H:%M')})")

            success = False
            attempts = 0

            while not success:
                attempts += 1
                print(f"\n--- Попытка №{attempts} ---")
                success = run_single_check()

                if success:
                    print("\n🎉 ОТЧЕТ ЗАГРУЖЕН! Жду следующего дня.")
                    break
                else:
                    print(f"\n💤 Отчет еще не пришел. Следующая проверка через {CHECK_INTERVAL // 60} мин...")
                    time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\n⏹ Остановка пользователем.")
            break
        except Exception as e:
            print(f"💥 Error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()