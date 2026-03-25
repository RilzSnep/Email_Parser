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

# 🛠 ИСПРАВЛЕНИЕ: Отключаем ВСЕ предупреждения (включая RequestsDependencyWarning)
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

# ⏰ ВРЕМЯ ЗАПУСКА (для теста поставьте текущее время + 1 минута)
START_HOUR = 15
START_MINUTE = 40
CHECK_INTERVAL = 60  # Для теста уменьшил до 1 минуты, чтобы быстрее видеть результат. Потом верните 300.


# ===============================================

def decode_mime_words(s):
    if not s: return ""
    decoded = decode_header(s)
    return ''.join(word.decode(encoding or 'utf8') if isinstance(word, bytes) else word for word, encoding in decoded)


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("   📩 Уведомление отправлено в Telegram")
        else:
            print(f"   ⚠️ Ошибка TG: {response.text}")
    except Exception as e:
        print(f"   ❌ Ошибка отправки в TG: {e}")


def check_db_for_date(target_date):
    """Проверяет наличие данных за дату в ClickHouse"""
    client = None
    try:
        client = Client(**CH_CONFIG)
        # Используем f-string для безопасной подстановки даты
        query = f"SELECT count() FROM {TABLE_NAME} WHERE event_date = '{target_date}'"
        result = client.execute(query)
        count = result[0][0] if result else 0
        return count > 0
    except Exception as e:
        print(f"   ❌ Ошибка проверки БД: {e}")
        return False
    finally:
        if client: client.disconnect()


def prepare_data_for_ch(df):
    df['event_date'] = pd.to_datetime(df['date']).dt.date
    df['event_hour'] = pd.to_datetime(df['hour']).dt.hour

    numeric_cols = ['publisher_id', 'section_id', 'bid_responses', 'responses',
                    'impressions', 'v_firstq', 'v_midpoint', 'v_thirdq', 'v_complete']
    for col in numeric_cols:
        if col in df.columns: df[col] = df[col].fillna(0).astype(int)

    decimal_cols = ['net_payable', 'actual_pub']
    for col in decimal_cols:
        if col in df.columns: df[col] = df[col].fillna(0.0).astype(float)

    columns_map = {
        'event_date': 'event_date', 'event_hour': 'event_hour',
        'publisher_id': 'publisher_id', 'section_name': 'section_name',
        'section_id': 'section_id', 'cp_bidder_name': 'cp_bidder_name',
        'bid_responses': 'bid_responses', 'responses': 'responses',
        'impressions': 'impressions', 'net_payable': 'net_payable',
        'actual_pub': 'actual_pub', 'v_firstq': 'v_firstq',
        'v_midpoint': 'v_midpoint', 'v_thirdq': 'v_thirdq',
        'v_complete': 'v_complete'
    }

    final_df = df[list(columns_map.keys())].rename(columns=columns_map)
    final_df['inserted_at'] = datetime.now()
    return final_df


def insert_to_clickhouse(df):
    client = None
    try:
        client = Client(**CH_CONFIG)
        data = df.to_dict('records')
        if not data: return 0, 0, 0, None

        query = f"""
            INSERT INTO {TABLE_NAME} 
            (event_date, event_hour, publisher_id, section_name, section_id, 
             cp_bidder_name, bid_responses, responses, impressions, net_payable, 
             actual_pub, v_firstq, v_midpoint, v_thirdq, v_complete, inserted_at)
            VALUES
        """
        client.execute(query, data)

        total_imp = df['impressions'].sum()
        total_rev = df['net_payable'].sum()
        report_date = df['event_date'].iloc[0]

        return len(data), total_imp, total_rev, report_date
    except Exception as e:
        print(f"   ❌ Ошибка БД: {e}")
        return 0, 0, 0, None
    finally:
        if client: client.disconnect()


def check_and_load():
    timestamp = datetime.now().strftime('%H:%M:%S')
    print(f"[{timestamp}] 🔍 Проверка статуса...")

    # Целевая дата - вчера
    target_date = (datetime.now() - timedelta(days=1)).date()
    print(f"   📅 Проверяем наличие отчета за: {target_date}")

    # 1. Проверка БД
    if check_db_for_date(target_date):
        print(f"   ✅ Отчет за {target_date} УЖЕ есть в базе!")
        print("   🧹 Очищаем почту (помечаем все письма с темой как прочитанные)...")
        mail = None
        try:
            mail = imaplib.IMAP4_SSL(IMAP_SERVER)
            mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
            mail.select("inbox")
            status, messages = mail.search(None, f'(SUBJECT "{SUBJECT_KEYWORD}")')
            if status == "OK":
                for email_id in messages[0].split():
                    mail.store(email_id, '+FLAGS', '\\Seen')
            print("   ✅ Почта очищена.")
        except:
            pass
        finally:
            if mail:
                try:
                    mail.close(); mail.logout()
                except:
                    pass
        return True

        # 2. Поиск на почте
    print(f"   ⏳ Отчета за {target_date} нет. Ищем на почте...")

    mail = None
    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER)
        mail.login(EMAIL_ACCOUNT, EMAIL_PASSWORD)
        mail.select("inbox")

        search_criteria = f'(UNSEEN SUBJECT "{SUBJECT_KEYWORD}")'
        status, messages = mail.search(None, search_criteria)

        if status != "OK": return False

        email_ids = messages[0].split()
        if not email_ids:
            print(f"   ℹ️ Новых непрочитанных писем не найдено.")
            return False

        print(f"   📬 Найдено кандидатов: {len(email_ids)}")

        for email_id in email_ids:
            status, msg_data = mail.fetch(email_id, "(RFC822)")
            if status != "OK": continue

            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode_mime_words(msg.get("Subject", ""))

            for part in msg.walk():
                content_disposition = str(part.get("Content-Disposition"))
                if "attachment" in content_disposition:
                    filename = part.get_filename()
                    filename = decode_mime_words(filename)

                    if filename and filename.lower().endswith('.csv'):
                        file_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        safe_name = f"{file_ts}_{filename}"
                        filepath = os.path.join(SAVE_FOLDER, safe_name)

                        with open(filepath, "wb") as f:
                            f.write(part.get_payload(decode=True))
                        print(f"   💾 Скачан: {safe_name}")

                        try:
                            df = pd.read_csv(filepath)
                            if df.empty:
                                print("   ⚠️ Файл пуст.")
                                continue

                            first_date_in_file = pd.to_datetime(df['date'].iloc[0]).date()

                            if first_date_in_file != target_date:
                                print(f"   ⚠️ Файл за дату {first_date_in_file}, а ждем {target_date}. Пропускаем.")
                                continue

                            df_prepared = prepare_data_for_ch(df)
                            rows_loaded, imp, rev, rep_date = insert_to_clickhouse(df_prepared)

                            if rows_loaded > 0:
                                print(f"   ✅ УСПЕХ! Загружено {rows_loaded} строк.")

                                msg_text = (
                                    f"📊 <b>Отчет выгружен!</b>\n\n"
                                    f"📅 Дата отчета: <b>{rep_date}</b>\n"
                                    f"📈 Impressions: <b>{imp:,}</b>\n"
                                    f"💰 Revenue: <b>{rev:,.2f} $</b>\n\n"
                                    f"✅ Данные добавлены в ClickHouse."
                                )
                                send_telegram_message(msg_text)

                                mail.store(email_id, '+FLAGS', '\\Seen')
                                return True
                            else:
                                print(f"   ⚠️ Файл прочитан, но данных для загрузки нет.")
                        except Exception as e:
                            print(f"   ❌ Ошибка обработки файла: {e}")

            # Помечаем письмо как прочитанное в любом случае, чтобы не зациклиться
            mail.store(email_id, '+FLAGS', '\\Seen')

        return False

    except Exception as e:
        print(f"   ❌ Ошибка соединения: {e}")
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
    print(f"⏳ Следующий старт в {target.strftime('%H:%M')} (через {sleep_time / 60:.1f} мин.)")
    print("💤 Скрипт спит...")
    time.sleep(sleep_time)


def main():
    if not os.path.exists(SAVE_FOLDER):
        os.makedirs(SAVE_FOLDER)

    print("=" * 60)
    print("🤖 Умный загрузчик + Telegram (Исправленный v2)")
    print(f"🕒 Старт: ежедневно в {START_HOUR:02d}:{START_MINUTE:02d}")
    print(f"🔍 Логика: Проверка БД -> Почта -> TG")
    print("=" * 60)

    while True:
        wait_until_start_time()

        print("\n" + "=" * 60)
        print(f"☀️ Проснулся! Начинаю работу ({datetime.now().strftime('%H:%M')})")
        print("=" * 60)

        success = False
        attempts = 0
        max_attempts = 288

        while not success and attempts < max_attempts:
            attempts += 1
            print(f"\n--- Попытка №{attempts} ---")

            success = check_and_load()

            if success:
                print("\n" + "=" * 60)
                print("🎉 ЗАДАЧА ВЫПОЛНЕНА! Жду следующего дня.")
                print("=" * 60)
                break
            else:
                print(f"💤 Пока без успеха. Следующая попытка через {CHECK_INTERVAL // 60} мин...")
                time.sleep(CHECK_INTERVAL)

        if not success:
            print("\n⚠️ Не удалось выгрузить отчет в течение суток.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⏹ Остановка пользователем.")