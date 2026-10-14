import hmac
from werkzeug.security import *
from os import environ
import time
import random
import gevent

def generate_hash(string):

    msg = bytes(string, "utf-16")

    return hmac.new(key=bytes(environ.get("MASTER_KEY"), "utf-16"),
                    msg=msg,
                    digestmod='md5'
                    ).hexdigest()


def validate_hash(string, hashstr):

    return hmac.compare_digest(hashstr, generate_hash(string))


def hash_password(password):

    return generate_password_hash(
        password, method='pbkdf2:sha512', salt_length=8)

def safe_compare(x, y):
    """Constant-time comparison of two secrets (MFA recovery codes). A missing value never matches."""
    if not x or not y:
        return False
    return hmac.compare_digest(str(x).encode("utf-8"), str(y).encode("utf-8"))
