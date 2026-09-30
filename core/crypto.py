"""
Шифрование пароля исходной базы на время ожидания в очереди.

Пароль нужен воркеру, который снимет копию позже, поэтому его приходится
где-то держать. Он хранится только зашифрованным (Fernet: AES + HMAC) и
стирается, как только задача завершилась любым исходом.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

__all__ = ["InvalidToken", "decrypt", "encrypt"]


def _fernet():
    material = settings.BACKUP_CREDENTIALS_KEY or settings.SECRET_KEY
    # Отдельный ключ, выведенный из секрета: утечка зашифрованного пароля
    # не упрощает подделку сессий, подписанных тем же SECRET_KEY.
    digest = hashlib.sha256(f"datastudio-credentials:{material}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value):
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token):
    return _fernet().decrypt(token.encode()).decode()
