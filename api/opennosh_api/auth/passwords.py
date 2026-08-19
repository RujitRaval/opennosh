from pwdlib import PasswordHash

_PASSWORD_HASH = PasswordHash.recommended()
_DUMMY_HASH = _PASSWORD_HASH.hash("opennosh-dummy-password-never-used")


def hash_password(password: str) -> str:
    return _PASSWORD_HASH.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _PASSWORD_HASH.verify(password, password_hash)


def perform_dummy_verification(password: str) -> None:
    _PASSWORD_HASH.verify(password, _DUMMY_HASH)
