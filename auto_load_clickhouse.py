import imaplib
import email
from email.header import decode_header
import os
import time
import pandas as pd
import requests
import warnings
from datetime import datetime, timedelta
from clickhouse_driver import Client

warnings.filterwarnings("ignore")

# ================= НАСТРОЙКИ =================
EMAIL_ACCOUNT = "a.vladimirov@limehd.tv"
EMAIL_PASSWORD = "gjtwejzhotmyalsx"
IMAP_SERVER = "imap.yandex.ru"
SAVE_FOLDER = "./BetweenX_Reports"
SUBJECT_KEYWORD = "BetweenX Report LimeHD"

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
START_MINUTE = 57
CHECK_INTERVAL = 300  # 5 минут
SCAN_LAST_COUNT = 10  # Чуть больше запас


# ===============================================

def decode_mime_words(s):
    if not s: return ""
    decoded = decode_header(s)
    return ''.join(word.decode(encoding or 'utf8') if isinstance(word, bytes) else word for word, encoding in decoded)


def send_telegram_message(text, is_alert=False):
    # Исправлено: убраны пробелы (на всякий случай)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    if is_alert:
        payload['text'] = f"🚨 <b>ALERT:</b>\n{text}"

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("   📩 TG: Отправлено")
        else:
            print(f"   ⚠️ TG Error: {response.text}")
    except Exception as e:
        print(f"   ❌ TG Exception: {e}")


def check_db_for_date(target_date):
    client = None
    try:
        client = Client(**CH_CONFIG)
        query = f"SELECT count() FROM {TABLE_NAME} WHERE event_date = '{target_date}'"
        result = client.execute(query)
        return (result[0][0] > 0) if result else False
    except Exception as e:
        print(f"   ❌ DB Check Error: {e}")
        return False
    finally:
        if client: client.disconnect()


def prepare_data_for_ch(df):
    # 1. Проверка обязательных колонок
    required = ['date', 'hour']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"В файле отсутствуют колонки: {missing}")

    df['event_date'] = pd.to_datetime(df['date']).dt.date
    df['event_hour'] = pd.to_datetime(df['hour']).dt.hour

    numeric_cols = ['publisher_id', 'section_id', 'bid_responses', 'responses',
                    'impressions', 'v_firstq', 'v_midpoint', 'v_thirdq', 'v_complete']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)

    decimal_cols = ['net_payable', 'actual_pub']
    for col in decimal_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0).astype(float)

    columns_map = {
        'event_date': 'event_date', 'event_hour': 'event_hour', 'publisher_id': 'publisher_id',
        'section_name': 'section_name', 'section_id': 'section_id', 'cp_bidder_name': 'cp_bidder_name',
        'bid_responses': 'bid_responses', 'responses': 'responses', 'impressions': 'impressions',
        'net_payable': 'net_payable', 'actual_pub': 'actual_pub', 'v_firstq': 'v_firstq',
        'v_midpoint': 'v_midpoint', 'v_thirdq': 'v_thirdq', 'v_complete': 'v_complete'
    }

    available_cols = [k for k in columns_map.keys() if k in df.columns]
    final_df = df[available_cols].rename(columns={k: columns_map[k] for k in available_cols})

    # 2. Проверка, что ключевые данные не пусты
    if 'impressions' not in final_df.columns or 'event_date' not in final_df.columns:
        raise ValueError("После маппинга отсутствуют ключевые колонки (impressions/event_date)")

    final_df['inserted_at'] = datetime.now()
    return final_df


def insert_to_clickhouse(df):
    client = None
    try:
        client = Client(**CH_CONFIG)
        data = df.to_dict('records')
        if not data: return 0, 0, 0, None

        cols = list(df.columns)
        query = f"INSERT INTO {TABLE_NAME} ({', '.join(cols)}) VALUES"
        client.execute(query, data)

        return len(data), int(df['impressions'].sum()), float(df['net_payable'].sum()), df['event_date'].iloc[0]
    except Exception as e:
        print(f"   ❌ DB Insert Error: {e}")
        raise e  # Пробрасываем ошибку выше, чтобы не пометить письмо как прочитанное
    finally:
        if client: client.disconnect()


def check_and_load():
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"[{timestamp}] 🔍 Запуск проверки почты...")

    mail = None
    loaded_count = 0
    error_occurred = False

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select("inbox")

        status, messages = mail.search(None, f'(SUBJECT "{SUBJECT_KEYWORD}")')
        if status != "OK":
            raise Exception(f"IMAP Search Error: {status}")

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

            if SUBJECT_KEYWORD not in subject: continue

            print(f"\n   📨 Обработка: {subject}")
            file_found = False
            load_success = False

            for part in msg.walk():
                if "attachment" in str(part.get("Content-Disposition")):
                    filename = decode_mime_words(part.get_filename())
                    if filename and filename.lower().endswith('.csv'):
                        file_found = True
                        print(f"      📎 Файл: {filename}")

                        file_data = part.get_payload(decode=True)
                        filepath = os.path.join(SAVE_FOLDER, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}")

                        with open(filepath, "wb") as f:
                            f.write(file_data)

                        try:
                            # Попытка чтения с авто-определением кодировки
                            try:
                                df = pd.read_csv(filepath)
                            except UnicodeDecodeError:
                                print("      ⚠️ UTF-8 не подошел, пробуем CP1251...")
                                df = pd.read_csv(filepath, encoding='cp1251')

                            if df.empty:
                                raise ValueError("Файл пуст")

                            report_date = pd.to_datetime(df['date'].iloc[0]).date()
                            print(f"      📅 Дата: {report_date}")

                            if check_db_for_date(report_date):
                                print(f"      ✅ Уже в БД")
                                load_success = True  # Считаем успешным, что обработали (данные есть)
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
                                        f"📈 Impressions: <b>{imp:,}</b>\n"
                                        f"💰 Revenue: <b>{rev:,.2f} $</b>"
                                    )
                                    send_telegram_message(msg_text)
                                else:
                                    raise ValueError("Загрузка вернула 0 строк")

                        except Exception as e:
                            print(f"      ❌ Ошибка обработки: {e}")
                            error_occurred = True
                            # Не помечаем как seen, чтобы повторить при следующем запуске!
                            # Но чтобы не зациклиться на битом файле вечно, можно добавить счетчик попыток (в прод. версии)
                            send_telegram_message(f"❌ Ошибка обработки файла {filename}:\n{str(e)}", is_alert=True)
                        break

            # Помечаем как прочитанное ТОЛЬКО если файл найден и обработка успешна (или данные уже есть)
            if file_found and (load_success or not error_occurred):
                # Логика: если была ошибка (error_occurred=True), мы НЕ помечаем, чтобы попробовать снова.
                # НО: если ошибка критическая (файл битый навсегда), мы зациклимся.
                # Компромисс: помечаем, если данные уже есть ИЛИ загрузка прошла успешно.
                if load_success:
                    mail.store(email_id, '+FLAGS', '\\Seen')
            elif not file_found:
                # Если письма без вложения - помечаем, чтобы не спамить
                mail.store(email_id, '+FLAGS', '\\Seen')

        print(f"\n--- Итог: загружено {loaded_count} ---")
        return loaded_count > 0

    except Exception as e:
        err_msg = f"Критическая ошибка скрипта: {e}"
        print(f"   ❌ {err_msg}")
        send_telegram_message(err_msg, is_alert=True)
        return False
    finally:
        if mail:
            try:
                mail.close(); mail.logout()
            except:
                pass


def wait_until_start_time():
    now = datetime.now()
    target = now.replace(hour=START_HOUR, minute=START_MINUTE, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)

    sleep_time = (target - now).total_seconds()

    # Предохранитель от сбоя часов
    if sleep_time < 0 or sleep_time > 90000:
        print("⚠️ Сбой времени! Ограничиваю сон 24 часами.")
        sleep_time = 86400

    print(f"⏳ Сон до {target.strftime('%H:%M')} ({sleep_time / 60:.1f} мин.)")
    time.sleep(sleep_time)


def main():
    if not os.path.exists(SAVE_FOLDER): os.makedirs(SAVE_FOLDER)

    print("=" * 60)
    print("🤖 Умный загрузчик v5 (Production)")
    print(f"🕒 Старт: {START_HOUR:02d}:{START_MINUTE:02d}")
    print("=" * 60)

    while True:
        try:
            wait_until_start_time()
            print(f"\n☀️ Проснулся! {datetime.now().strftime('%H:%M')}")
            success = check_and_load()

            if success:
                print("\n🎉 Готово! Жду следующего дня.")
            else:
                print("\nℹ️ Новых данных нет или были ошибки.")

            print("💤 До завтра...")
        except KeyboardInterrupt:
            print("\n⏹ Остановка пользователем.")
            break
        except Exception as e:
            print(f"💥 Unexpected Error in Main Loop: {e}")
            time.sleep(60)  # Пауза перед перезапуском


if __name__ == "__main__":
    main()