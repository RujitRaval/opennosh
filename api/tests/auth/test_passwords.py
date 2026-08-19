from opennosh_api.auth.passwords import hash_password, verify_password


def test_passwords_use_argon2id_and_verify_without_plaintext_storage() -> None:
    password_hash = hash_password("correct horse battery staple")

    assert password_hash.startswith("$argon2id$")
    assert "correct horse" not in password_hash
    assert verify_password("correct horse battery staple", password_hash)
    assert not verify_password("incorrect password", password_hash)
