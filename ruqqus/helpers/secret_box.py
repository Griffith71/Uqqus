"""At-rest encryption for the one DB value that genuinely needs it: the
Matrix recovery key stored on ChatIdentity (see ruqqus/classes/chat.py).
No other secret in this codebase is encrypted at rest - MASTER_KEY is
otherwise only ever used for HMAC signing (ruqqus/helpers/security.py) -
but a Matrix recovery key decrypts a user's entire message history, which
is a meaningfully higher stakes value than the things stored in plaintext
elsewhere (mfa_secret, OAuth tokens), so it gets its own symmetric cipher
rather than reusing that convention.

This does NOT make the key inaccessible to the operator - anyone with
MASTER_KEY and DB access can decrypt it, by design (see the recovery_key
routes in ruqqus/routes/chat.py). It only protects against the key sitting
around in plaintext in the database itself (backups, dumps, read replicas,
etc).
"""

import base64
import hashlib
from os import environ

from cryptography.fernet import Fernet


def _fernet():
    key = base64.urlsafe_b64encode(hashlib.sha256(environ.get("MASTER_KEY").encode()).digest())
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
