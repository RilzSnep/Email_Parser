import os
import sys
from datetime import datetime

import pandas as pd
from clickhouse_driver import Client
from dotenv import load_dotenv

load_dotenv()


def build_client() -> Client:
    host = os.getenv("CH_HOST")
    port = int(os.getenv("CH_PORT", "9000"))
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
    output_dir = os.getenv("CH_OUTPUT_DIR", ".")
    output_format = os.getenv("CH_OUTPUT_FORMAT", "csv").strip().lower()

    if not table_name:
        print("Ошибка: не задан TABLE_NAME в .env")
        sys.exit(1)

    if output_format not in {"csv", "xlsx"}:
        print("Ошибка: CH_OUTPUT_FORMAT должен быть 'csv' или 'xlsx'")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    client = None
    try:
        client = build_client()

        query = f"""
        SELECT
            event_date,
            event_hour,
            publisher_id,
            section_name,
            section_id,
            cp_bidder_name,
            bid_responses,
            responses,
            impressions,
            net_payable,
            actual_pub,
            v_firstq,
            v_midpoint,
            v_thirdq,
            v_complete,
            inserted_at,

            replaceOne(cp_bidder_name, 'Lime4DSP - ', '') AS DSP_NAME,

            multiIf(
                positionCaseInsensitiveUTF8(section_name, 'VPAID Wrapper') > 0
                    AND (
                        positionCaseInsensitiveUTF8(section_name, 'smart') > 0
                        OR positionCaseInsensitiveUTF8(section_name, 'tv') > 0
                        OR positionCaseInsensitiveUTF8(section_name, 'tizen') > 0
                        OR positionCaseInsensitiveUTF8(section_name, 'webos') > 0
                        OR positionCaseInsensitiveUTF8(section_name, 'android tv') > 0
                        OR positionCaseInsensitiveUTF8(section_name, 'apple tv') > 0
                        OR positionCaseInsensitiveUTF8(section_name, 'ctv') > 0
                    ),
                    'CTV',

                positionCaseInsensitiveUTF8(section_name, 'VPAID Wrapper') > 0
                    AND (
                        positionCaseInsensitiveUTF8(section_name, 'mobile') > 0
                        OR positionCaseInsensitiveUTF8(section_name, 'phone') > 0
                        OR positionCaseInsensitiveUTF8(section_name, 'ios') > 0
                        OR (
                            positionCaseInsensitiveUTF8(section_name, 'android') > 0
                            AND positionCaseInsensitiveUTF8(section_name, 'android tv') = 0
                        )
                    ),
                    'Mobile',

                ''
            ) AS PLATFORM,

            round(
                if(
                    bid_responses = 0,
                    0.0,
                    (toFloat64(responses) / toFloat64(bid_responses)) * 100
                ),
                2
            ) AS FILL_RATE,

            round(
                if(
                    responses = 0,
                    0.0,
                    (toFloat64(impressions) / toFloat64(responses)) * 100
                ),
                2
            ) AS SHOW_RATE,

            round(
                if(
                    impressions = 0,
                    0.0,
                    (toFloat64(net_payable) / toFloat64(impressions)) * 1000
                ),
                2
            ) AS CPM,

            'DSP' AS INVENTORY_TYPE

        FROM {table_name}
        ORDER BY event_date DESC, event_hour DESC
        """

        data_rows, columns_with_types = client.execute(query, with_column_types=True)
        columns = [col[0] for col in columns_with_types]

        df = pd.DataFrame(data_rows, columns=columns)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if output_format == "csv":
            output_path = os.path.join(output_dir, f"{table_name}_{timestamp}.csv")
            df.to_csv(output_path, index=False, encoding="utf-8-sig")
        else:
            output_path = os.path.join(output_dir, f"{table_name}_{timestamp}.xlsx")
            df.to_excel(output_path, index=False)

        print(f"База данных: {os.getenv('CH_DATABASE')}")
        print(f"Таблица: {table_name}")
        print(f"Строк выгружено: {len(df)}")
        print(f"Столбцов выгружено: {len(df.columns)}")
        print(f"Файл сохранен: {output_path}")

    except Exception as e:
        print(f"Ошибка при выгрузке таблицы: {e}")
        sys.exit(1)
    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    main()