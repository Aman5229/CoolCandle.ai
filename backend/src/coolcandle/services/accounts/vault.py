"""Encrypts credentials with a key that lives only in the environment."""

import json

from cryptography.fernet import Fernet


class Vault:
    def __init__(self, key: str) -> None:
        self._fernet = Fernet(key.encode())

    def encrypt(self, plain: str) -> str:
        return self._fernet.encrypt(plain.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()

    def encrypt_dict(self, values: dict[str, str]) -> str:
        return self.encrypt(json.dumps(values))

    def decrypt_dict(self, token: str) -> dict[str, str]:
        values: dict[str, str] = json.loads(self.decrypt(token))
        return values
