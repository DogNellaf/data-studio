import os
import subprocess
from datastudio.settings import MEDIA_DIR
import psycopg2


def full_db_backup(host, port, user, password, database, backup_name) -> bool:
    """
    Выполняет полное копирование базы данных PostgreSQL в файл.

    :param host: IP-адрес или хост базы данных
    :param port: Порт базы данных
    :param user: Имя пользователя для подключения
    :param password: Пароль для подключения
    :param database: Имя базы данных
    :param backup_name: Название файла бэкапа
    :return: Boolean результат формирования бэкапа
    """

    if not os.path.exists(MEDIA_DIR):
        os.makedirs(MEDIA_DIR)

    backup_path = os.path.join(MEDIA_DIR, backup_name + '.sql')
    
    # Формируем команду для pg_dump
    dump_command = [
        "C:\Program Files\PostgreSQL\\17\\bin\pg_dump",
        f"--host={host}",
        f"--port={port}",
        f"--username={user}",
        f"--dbname={database}",
        f"--file={backup_path}"
    ]

    try:
        env = os.environ.copy()
        env["PGPASSWORD"] = password

        subprocess.run(dump_command, check=True, env=env)

        print(f"Бэкап успешно сохранён в: {backup_path}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Ошибка при выполнении pg_dump: {e}")
    except Exception as e:
        print(f"Общая ошибка: {e}")
    return False

def incremental_db_backup(host, port, user, password, database, last_backup_time, backup_name):
    """
    Выполняет инкрементальное резервное копирование PostgreSQL с использованием pgdumplib.

    :param host: Хост базы данных
    :param port: Порт базы данных
    :param user: Имя пользователя
    :param password: Пароль
    :param database: Имя базы данных
    :param last_backup_time: Дата и время последнего бэкапа в формате "YYYY-MM-DD HH:MM:SS"
    :param backup_name: Название файла резервной копии
    :return: Boolean результат формирования бэкапа
    """

    if not os.path.exists(MEDIA_DIR):
        os.makedirs(MEDIA_DIR)

    backup_path = os.path.join(MEDIA_DIR, backup_name + '.sql')

    try:
        connection = psycopg2.connect(
            host=host, port=port, user=user, password=password, dbname=database
        )
        cursor = connection.cursor()

        with open(backup_path, "w") as backup_file:
            cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public';")
            tables = cursor.fetchall()
            for table in tables:
                table_name = table[0]
                cursor.execute(
                    f"""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = '{table_name}' AND column_name IN ('updated_at', 'created_at');
                    """
                )
                timestamp_columns = cursor.fetchall()

                if timestamp_columns:
                    timestamp_column = timestamp_columns[0][0]
                    query = f"SELECT * FROM {table_name} WHERE {timestamp_column} > %s;"
                    cursor.execute(query, (last_backup_time,))
                    rows = cursor.fetchall()

                    if rows:
                        for row in rows:
                            values = ", ".join(["'%s'" % str(v).replace("'", "''") for v in row])
                            insert_statement = f"INSERT INTO {table_name} VALUES ({values});\n"
                            backup_file.write(insert_statement)

        print(f"Инкрементальный бэкап успешно создан: {backup_path}")
        return True

    except Exception as e:
        print(f"Ошибка при создании резервной копии: {e}")
        return False

    finally:
        if connection:
            cursor.close()
            connection.close()


def differential_db_backup(host, port, user, password, database, last_full_backup_time, backup_name) -> bool:
    """
    Выполняет дифференциальное резервное копирование PostgreSQL, экспортируя все изменения с момента последнего полного бэкапа.

    :param host: Хост базы данных
    :param port: Порт базы данных
    :param user: Имя пользователя
    :param password: Пароль
    :param database: Имя базы данных
    :param last_full_backup_time: Дата и время последнего полного бэкапа в формате "YYYY-MM-DD HH:MM:SS"
    :param backup_name: Название файла резервной копии
    :return: Boolean результат формирования бэкапа
    """
    if not os.path.exists(MEDIA_DIR):
        os.makedirs(MEDIA_DIR)

    backup_path = os.path.join(MEDIA_DIR, backup_name + '.sql')

    try:
        connection = psycopg2.connect(
            host=host, port=port, user=user, password=password, dbname=database
        )
        cursor = connection.cursor()

        with open(backup_path, "w") as backup_file:
            cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public';")
            tables = cursor.fetchall()

            for table in tables:
                table_name = table[0]

                cursor.execute(
                    f"""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = '{table_name}' AND column_name IN ('updated_at', 'created_at');
                    """
                )
                timestamp_columns = cursor.fetchall()

                if timestamp_columns:
                    timestamp_column = timestamp_columns[0][0]
                    query = f"SELECT * FROM {table_name} WHERE {timestamp_column} > %s;"
                    cursor.execute(query, (last_full_backup_time,))
                    rows = cursor.fetchall()

                    if rows:
                        for row in rows:
                            values = ", ".join(["'%s'" % str(v).replace("'", "''") for v in row])
                            insert_statement = f"INSERT INTO {table_name} VALUES ({values});\n"
                            backup_file.write(insert_statement)

        print(f"Дифференциальный бэкап успешно создан: {backup_path}")
        return True

    except Exception as e:
        print(f"Ошибка при создании резервной копии: {e}")
        return False

    finally:
        if connection:
            cursor.close()
            connection.close()
