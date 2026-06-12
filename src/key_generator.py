"""Generate an RSA key pair for WhatsApp Flow endpoint testing."""

from __future__ import annotations

import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_key_pair(passphrase: str) -> tuple[str, str]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase.encode("utf-8")),
    ).decode("utf-8")

    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")

    return private_pem, public_pem


def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1]:
        raise SystemExit(
            "Passphrase is empty. Please include passphrase argument to generate the keys like: python -m src.key_generator {passphrase}"
        )

    passphrase = sys.argv[1]
    private_key, public_key = generate_key_pair(passphrase)

    print("\n===== PRIVATE KEY (set as PRIVATE_KEY in .env) =====")
    print(private_key)
    print("===== PUBLIC KEY (upload to Meta Business Manager → WhatsApp → Flows) =====")
    print(public_key)


if __name__ == "__main__":
    main()