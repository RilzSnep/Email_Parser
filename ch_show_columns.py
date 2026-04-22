import os
import sys
from clickhouse_driver import Client
from dotenv import load_dotenv

load_dotenv()


def build_client() -> Client:
    host = os.getenv("CH_HOST")
    port = os.getenv("CH_PORT", "9000")
    user = os.getenv("CH_USER")
    password = os.getenv("CH_PASSWORD")
    database = os.getenv("CH_DATABASE")

    return Client(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
    )


def main() -> None:
    table_name = os.getenv("TABLE_NAME")

    client = None
    try:
        client = build_client()

        query = """
        SELECT
            position,
            name,
            type,
            default_kind,
            default_expression
        FROM system.columns
        WHERE database = currentDatabase()
          AND table = %(table_name)s
        ORDER BY position
        """

        rows = client.execute(query, {"table_name": table_name})

        if not rows:
            print(f"Таблица '{table_name}' не найдена или в ней нет столбцов.")
            sys.exit(1)

        print(f"База данных: {os.getenv('CH_DATABASE')}")
        print(f"Таблица: {table_name}")
        print("-" * 140)
        print(f"{'№':<6} {'name':<35} {'type':<30} {'default_kind':<20} {'default_expression'}")
        print("-" * 140)

        for row in rows:
            position, name, col_type, default_kind, default_expression = row
            default_kind = default_kind or ""
            default_expression = default_expression or ""
            print(f"{position:<6} {name:<35} {col_type:<30} {default_kind:<20} {default_expression}")

        print("-" * 140)
        print(f"Всего столбцов: {len(rows)}")

    except Exception as e:
        print(f"Ошибка при получении структуры таблицы: {e}")
        sys.exit(1)
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    main()