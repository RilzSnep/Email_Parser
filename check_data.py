from clickhouse_driver import Client
import pandas as pd

# Настройки
CH_CONFIG = {
    'host': '172.19.95.127',
    'port': 9000,
    'user': 'sandbox_analytic',
    'password': 'fy7$ndDOjp.r',
    'database': 'sandbox'
}

TARGET_DATE = '2026-03-21'


def view_readable_table():
    client = None
    try:
        print(f"🔌 Подключение к {CH_CONFIG['host']}...")
        client = Client(**CH_CONFIG)

        # Запрос БЕЗ форматирования времени (просто число часа)
        query = f"""
            SELECT 
                event_date,
                event_hour,
                publisher_id,
                section_name,
                cp_bidder_name,
                SUM(impressions) as impressions,
                SUM(net_payable) as revenue
            FROM vtochku_between
            WHERE event_date = '{TARGET_DATE}'
            GROUP BY event_date, event_hour, publisher_id, section_name, cp_bidder_name
            ORDER BY event_hour ASC, publisher_id ASC
        """

        print(f"📊 Загрузка данных за {TARGET_DATE}...")
        rows = client.execute(query)

        if not rows:
            print("❌ Данные не найдены.")
            return

        df = pd.DataFrame(rows, columns=[
            'Дата', 'Час', 'Pub ID', 'Секция', 'Биддер', 'Показы', 'Доход'
        ])

        # Настройки Pandas для читаемости
        pd.set_option('display.max_rows', None)
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', 1000)
        pd.set_option('display.colheader_justify', 'center')
        pd.set_option('display.float_format', '{:.2f}'.format)

        # Обрезаем длинные тексты, чтобы таблица влезала в экран
        df['Секция'] = df['Секция'].apply(lambda x: str(x)[:25] + '...' if len(str(x)) > 25 else x)
        df['Биддер'] = df['Биддер'].apply(lambda x: str(x)[:20] + '...' if len(str(x)) > 20 else x)

        print("\n" + "=" * 110)
        print(f"ОТЧЕТ ЗА {TARGET_DATE} (Всего строк: {len(df)})")
        print("=" * 110)

        # Вывод таблицы
        print(df.to_string(index=False))

        print("\n" + "=" * 110)
        print(f"💰 Итого показов: {df['Показы'].sum():,}")
        print(f"💰 Итого доход: {df['Доход'].sum():,.2f}")
        print("=" * 110)

    except Exception as e:
        print(f"\n❌ Ошибка подключения или выполнения: {e}")
        print("\n💡 Совет: Проверьте, включен ли VPN и доступен ли сервер 172.19.95.127 (попробуйте сделать ping).")
    finally:
        if client:
            client.disconnect()


if __name__ == "__main__":
    view_readable_table()