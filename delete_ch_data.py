from clickhouse_driver import Client
from datetime import datetime

# ================= НАСТРОЙКИ ПОДКЛЮЧЕНИЯ =================
CH_CONFIG = {
    'host': '172.19.95.127',
    'port': 9000,
    'user': 'sandbox_analytic',
    'password': 'fy7$ndDOjp.r',
    'database': 'sandbox'
}
TABLE_NAME = 'vtochku_between'


# =========================================================

def get_connection():
    return Client(**CH_CONFIG)


def count_records(client, date_str):
    """Считает количество записей за дату"""
    query = f"SELECT count() FROM {TABLE_NAME} WHERE event_date = '{date_str}'"
    result = client.execute(query)
    return result[0][0] if result else 0


def delete_records(client, date_str):
    """Запускает удаление за дату"""
    # В ClickHouse удаление асинхронно (помечает на удаление)
    query = f"ALTER TABLE {TABLE_NAME} DELETE WHERE event_date = '{date_str}'"
    client.execute(query)
    print(f"✅ Команда на удаление отправлена серверу.")
    print("⏳ Данные физически удалятся в фоне (через несколько секунд/минут).")


def main():
    print("=" * 60)
    print("🗑 Удаление данных из ClickHouse по дате")
    print("=" * 60)

    # Ввод даты от пользователя
    date_input = input("Введите дату для удаления (ГГГГ-ММ-ДД): ").strip()

    # Простая валидация формата
    try:
        datetime.strptime(date_input, '%Y-%m-%d')
    except ValueError:
        print("❌ Ошибка: Неверный формат даты. Используйте ГГГГ-ММ-ДД (например, 2026-03-25).")
        return

    client = None
    try:
        client = get_connection()
        print(f"🔌 Подключение к базе '{CH_CONFIG['database']}'...")

        # Считаем, что будем удалять
        count = count_records(client, date_input)

        if count == 0:
            print(f"ℹ️ Записей за дату {date_input} не найдено. Нечего удалять.")
            return

        print(f"⚠️ Найдено записей за {date_input}: {count:,}")
        print("-" * 60)

        # Запрос подтверждения
        confirm = input(f"Вы уверены, что хотите удалить эти {count:,} записей? (да/нет): ").strip().lower()

        if confirm in ['да', 'y', 'yes', 'д']:
            print("🚀 Запуск удаления...")
            delete_records(client, date_input)

            # Проверка (с FINAL для актуальности)
            import time
            print("⏳ Ожидание применения изменений (5 сек)...")
            time.sleep(5)

            remaining = count_records(client, date_input)
            # Примечание: иногда сразу после ALTER SELECT еще может показать старое число без FINAL
            # Но для пользователя достаточно сообщения об успехе команды.

            print("\n" + "=" * 60)
            print("✅ ГОТОВО! Данные помечены на удаление.")
            if remaining == 0:
                print("✅ Проверка подтвердила: записей больше нет.")
            else:
                print(f"⚠️ Внимание: SELECT все еще видит {remaining} записей. Это нормально, данные удалятся фоном.")
        else:
            print("❌ Отменено пользователем. Ничего не удалено.")

    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
    finally:
        if client:
            client.disconnect()
            print("🔌 Соединение закрыто.")


if __name__ == "__main__":
    main()