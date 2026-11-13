#!/usr/bin/env python3
"""Generate local-dev secrets without committing them.

Writes MASTER_KEY, MATRIX_AS_TOKEN, MATRIX_HS_TOKEN, TURN_SHARED_SECRET,
OIDC_CLIENT_SECRET, OIDC_PRIVATE_KEY, VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY to the untracked .env (docker compose
reads it automatically) and renders, all untracked: the Synapse appservice
registration, the call relay's config (coturn/turnserver.conf), the relay
secret Synapse reads (synapse/turn_shared_secret) and the client secret
Synapse signs in to the site with (synapse/oidc_client_secret). Existing .env
values are never overwritten. A new .env (one with no MASTER_KEY yet) also gets
CHAT_STORE_SECRET, the secret the chat store keys are made from
(helpers/chat_store_keys.py); an existing one is left without it, so the store
keys of a dev site already in use don't change under it (they fall back to
MASTER_KEY).

Usage: python scripts/gen_dev_env.py
Production: set these from your secret store; never reuse the dev values.
"""
import base64
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
TEMPLATE = ROOT / "synapse" / "ruqqus-appservice.yaml.template"
RENDERED = ROOT / "synapse" / "ruqqus-appservice.yaml"
TURN_TEMPLATE = ROOT / "coturn" / "turnserver.conf.template"
TURN_RENDERED = ROOT / "coturn" / "turnserver.conf"
TURN_SECRET_FILE = ROOT / "synapse" / "turn_shared_secret"
OIDC_SECRET_FILE = ROOT / "synapse" / "oidc_client_secret"

GENERATORS = {
    "MASTER_KEY": lambda: base64.b64encode(secrets.token_bytes(32)).decode(),
    "MATRIX_AS_TOKEN": lambda: secrets.token_hex(32),
    "MATRIX_HS_TOKEN": lambda: secrets.token_hex(32),
    "TURN_SHARED_SECRET": lambda: secrets.token_hex(32),
    "OIDC_CLIENT_SECRET": lambda: secrets.token_hex(32),
    "OIDC_PRIVATE_KEY": lambda: oidc_private_key(),
    # push notifications' key pair (ruqqus/helpers/web_push.py): made together, so the two always match
    "VAPID_PRIVATE_KEY": lambda: vapid_pair()[0],
    "VAPID_PUBLIC_KEY": lambda: vapid_pair()[1],
}
# made only with a new set of secrets (no MASTER_KEY in .env yet): see the docstring
NEW_ENV_ONLY = {
    "CHAT_STORE_SECRET": lambda: secrets.token_hex(32),
}


_vapid = []


def vapid_pair():
    """A P-256 key pair for push notifications, as base64url (the private number, the uncompressed public point)."""
    if not _vapid:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError:
            print("No cryptography package: VAPID keys left empty, so push notifications stay off")
            _vapid.extend(["", ""])
            return _vapid
        key = ec.generate_private_key(ec.SECP256R1())
        number = key.private_numbers().private_value.to_bytes(32, "big")
        public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        _vapid.extend([base64.urlsafe_b64encode(x).rstrip(b"=").decode() for x in (number, public)])
    return _vapid


def oidc_private_key():
    """The key the site signs its OIDC id_tokens with: an RSA key's PEM, base64-encoded so it fits on one .env line."""
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        # no `cryptography` here (it is in the app's image, not always beside this script): OpenSSL makes the same key
        try:
            pem = subprocess.run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048"],
                                 check=True, capture_output=True).stdout
        except (OSError, subprocess.CalledProcessError):
            print("No cryptography package or openssl: OIDC_PRIVATE_KEY left empty, so chat apps stay off")
            return ""
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return base64.b64encode(pem).decode()


def write(path, content):
    # newline="" writes the text as it is (LF) on every system: coturn would read a Windows line ending as part of a
    # value, the secret among them
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def read_env():
    values = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def main():
    values = read_env()
    new_lines = []
    generators = dict(GENERATORS, **(NEW_ENV_ONLY if not values.get("MASTER_KEY") else {}))
    for key, make in generators.items():
        if not values.get(key):
            values[key] = make()
            new_lines.append(f"{key}={values[key]}")
    if new_lines:
        existing = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
        sep = "" if not existing or existing.endswith("\n") else "\n"
        ENV.write_text(existing + sep + "\n".join(new_lines) + "\n", encoding="utf-8")
        print(f"Wrote {', '.join(k.split('=')[0] for k in new_lines)} to .env")
    else:
        print(".env already has all secrets; left unchanged")

    rendered = TEMPLATE.read_text(encoding="utf-8")
    rendered = rendered.replace("__MATRIX_AS_TOKEN__", values["MATRIX_AS_TOKEN"])
    rendered = rendered.replace("__MATRIX_HS_TOKEN__", values["MATRIX_HS_TOKEN"])
    write(RENDERED, rendered)
    print(f"Rendered {RENDERED.relative_to(ROOT)} (untracked)")

    turn = TURN_TEMPLATE.read_text(encoding="utf-8").replace("__TURN_SHARED_SECRET__", values["TURN_SHARED_SECRET"])
    write(TURN_RENDERED, turn)
    write(TURN_SECRET_FILE, values["TURN_SHARED_SECRET"])       # Synapse strips the file's content
    print(f"Rendered {TURN_RENDERED.relative_to(ROOT)} and {TURN_SECRET_FILE.relative_to(ROOT)} (untracked)")

    write(OIDC_SECRET_FILE, values["OIDC_CLIENT_SECRET"])      # Synapse reads its client secret from this file
    print(f"Wrote {OIDC_SECRET_FILE.relative_to(ROOT)} (untracked)")


if __name__ == "__main__":
    main()
