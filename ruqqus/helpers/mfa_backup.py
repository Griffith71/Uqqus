"""Two-factor backup codes: the rules (no I/O; the SQL is `mfa_backup_store.py`).

An account with two-factor sign-in on can make ten one-time codes. A code is ten characters from an alphabet without
the look-alikes (no 0/O, 1/I), about 50 bits, shown once as `ABCDE-FGHJK` and kept only as a salted SHA-256: the site
cannot show them again, and a copy of the table opens nothing. Each is accepted once, and using one signs in WITHOUT
turning two-factor off (the older recovery code does turn it off, which is why codes replace it).

The older "recovery code" (`User.mfa_removal_code`) is one value worked out from the secret: never rotated, reusable,
and enough to switch two-factor off. It keeps working only for an account that has no backup codes yet, so nobody is
locked out by this change; once an account has made codes it stops working (`legacy_code_allowed`).
"""
import hashlib
import hmac
import secrets

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # 32 characters: 5 bits each, no 0 O 1 I
CODE_LENGTH = 10
COUNT = 10
LOW = 2                                              # "running low" from here down


def generate():
    """`COUNT` fresh, different codes, as typed (no hyphen)."""
    codes = []
    while len(codes) < COUNT:
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        if code not in codes:
            codes.append(code)
    return codes


def display(code):
    """`ABCDE-FGHJK`: how a code is shown."""
    return f"{code[:5]}-{code[5:]}"


def normalise(text):
    """The code a member typed, as stored (upper case, no spaces or hyphens), or None if it cannot be one. A
    six-digit authenticator code or the long legacy recovery code never normalises to a backup code."""
    code = "".join(str(text or "").upper().split()).replace("-", "")
    if len(code) != CODE_LENGTH or any(ch not in ALPHABET for ch in code):
        return None
    return code


def new_salt():
    return secrets.token_hex(8)


def digest(salt, code):
    return hashlib.sha256(f"{salt}:{code}".encode("utf-8")).hexdigest()


def matches(salt, stored_hash, code):
    """Constant-time comparison of a typed code with a stored hash."""
    return hmac.compare_digest(digest(salt, code), str(stored_hash).strip())


def legacy_code_allowed(has_codes):
    """The derived recovery code works only for an account that has never made backup codes."""
    return not has_codes


def status_line(unused, has_codes):
    """What Settings says about the codes: (text, running_low)."""
    if not has_codes:
        return "You haven't made backup codes yet.", True
    if unused == 0:
        return "You have no unused backup codes left.", True
    word = "code" if unused == 1 else "codes"
    return f"{unused} unused backup {word} left.", unused <= LOW
