import logging

import psycopg2

from django.conf import settings

logger = logging.getLogger(__name__)


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
    connection = None
    try:
        connection = psycopg2.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=database,
            connect_timeout=getattr(settings, "DB_CONNECT_TIMEOUT", 5),
        )
        return True
    except psycopg2.Error as exc:
        logger.warning("Не удалось подключиться к базе %s@%s:%s: %s", database, host, port, exc)
        return False
    finally:
        if connection is not None:
            connection.close()
