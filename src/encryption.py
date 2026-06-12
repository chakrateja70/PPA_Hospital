"""WhatsApp Flow request encryption helpers."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass
class FlowEndpointException(Exception):
    status_code: int
    message: str

    def __str__(self) -> str:
        return self.message


def decrypt_request(body: dict[str, Any], private_pem: str, passphrase: str) -> dict[str, Any]:
    encrypted_aes_key = body["encrypted_aes_key"]
    encrypted_flow_data = body["encrypted_flow_data"]
    initial_vector = body["initial_vector"]

    try:
        private_key = serialization.load_pem_private_key(
            private_pem.encode("utf-8"), password=passphrase.encode("utf-8") if passphrase else None
        )
    except Exception as error:
        raise FlowEndpointException(500, "Failed to load private key. Check PRIVATE_KEY and PASSPHRASE in .env.") from error

    try:
        decrypted_aes_key = private_key.decrypt(
            base64.b64decode(encrypted_aes_key),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except Exception as error:
        raise FlowEndpointException(421, "Failed to decrypt the request. Please verify your private key.") from error

    flow_data_buffer = base64.b64decode(encrypted_flow_data)
    initial_vector_buffer = base64.b64decode(initial_vector)

    tag_length = 16
    encrypted_flow_data_body = flow_data_buffer[:-tag_length]
    encrypted_flow_data_tag = flow_data_buffer[-tag_length:]

    decipher = AESGCM(decrypted_aes_key)
    try:
        decrypted_json_string = decipher.decrypt(
            initial_vector_buffer,
            encrypted_flow_data_body + encrypted_flow_data_tag,
            None,
        ).decode("utf-8")
    except Exception as error:
        raise FlowEndpointException(421, "Failed to decrypt the request body. AES-GCM tag mismatch.") from error

    return {
        "decryptedBody": json.loads(decrypted_json_string),
        "aesKeyBuffer": decrypted_aes_key,
        "initialVectorBuffer": initial_vector_buffer,
    }


def encrypt_response(response: dict[str, Any], aes_key_buffer: bytes, initial_vector_buffer: bytes) -> str:
    flipped_iv = bytes((~byte) & 0xFF for byte in initial_vector_buffer)
    cipher = AESGCM(aes_key_buffer)
    encrypted = cipher.encrypt(flipped_iv, json.dumps(response).encode("utf-8"), None)
    return base64.b64encode(encrypted).decode("utf-8")