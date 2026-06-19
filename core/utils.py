import psycopg2
from psycopg2 import OperationalError

def check_db_connection(host, port, user, password, database):
    """
    Проверяет соединение с PostgreSQL базой данных.

    :param host: IP-адрес или хост базы данных
    :param port: Порт базы данных
    :param user: Имя пользователя для подключения
    :param password: Пароль для подключения
    :param database: Имя базы данных
    :return: True, если соединение успешно установлено, иначе False
    """
    try:
        connection = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=database
        )
        connection.close()
        return True
    except:
        return False
