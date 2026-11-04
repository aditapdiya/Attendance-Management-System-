"""Aadhaar encryption at rest (Fernet / AES-128-CBC + HMAC) and verification helpers."""
import base64
import hashlib
import hmac
import re

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError

_D = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],
      [4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],
      [8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]]
_P = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],
      [9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def normalize_aadhaar(value: str) -> str:
    return re.sub(r"[\s-]", "", value or "")


def validate_aadhaar(value: str) -> str:
    n = normalize_aadhaar(value)
    if not re.fullmatch(r"[2-9]\d{11}", n):
        raise ValidationError("Aadhaar must be 12 digits and cannot start with 0 or 1.")
    if not verhoeff_valid(n):
        raise ValidationError("This Aadhaar number fails the checksum. Please re-check it.")
    return n


def _raw_key() -> bytes:
    k = settings.AADHAAR_ENCRYPTION_KEY
    if k:
        return k.encode()
    if settings.DEBUG:  # dev convenience only
        return base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    raise ImproperlyConfigured("AADHAAR_ENCRYPTION_KEY must be set when DEBUG is off.")


def _fernet() -> Fernet:
    return Fernet(_raw_key())


def encrypt(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise ValueError("Aadhaar could not be decrypted (wrong key?)")


def blind_index(plain: str) -> str:
    """Keyed hash so uniqueness can be enforced without decrypting every row."""
    return hmac.new(_raw_key(), plain.encode(), hashlib.sha256).hexdigest()
