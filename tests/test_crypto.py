import pytest
from app.utils import crypto


def test_round_trip():
    token = "EAABsbCS1iHgBO7ZC..."
    encrypted = crypto.encrypt(token)
    assert encrypted != token
    assert crypto.decrypt(encrypted) == token


def test_ciphertext_differs_each_time():
    a = crypto.encrypt("same-token")
    b = crypto.encrypt("same-token")
    assert a != b
    assert crypto.decrypt(a) == crypto.decrypt(b) == "same-token"


def test_decrypt_with_wrong_key_raises():
    from cryptography.fernet import Fernet
    other = Fernet(Fernet.generate_key())
    foreign = other.encrypt(b"secret").decode()
    with pytest.raises(crypto.EncryptionUnavailable):
        crypto.decrypt(foreign)


def test_decrypt_empty_raises():
    with pytest.raises(crypto.EncryptionUnavailable):
        crypto.decrypt("")


def test_available():
    assert crypto.is_available() is True
