import os
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from utils.crypto import CryptoManager


@pytest.fixture
def crypto(tmp_path, monkeypatch):
    monkeypatch.setattr('utils.crypto.get_app_data_dir', lambda: str(tmp_path))
    return CryptoManager()


def _load(path, password=None):
    with open(path, 'rb') as f:
        return serialization.load_pem_private_key(
            f.read(), password=password, backend=default_backend())


def test_generate_encrypted_with_passphrase(crypto):
    priv, pub, passphrase = crypto.generate_ssh_key_pair(passphrase='pw-abc')
    assert passphrase == 'pw-abc'
    with open(priv, 'rb') as f:
        assert b'ENCRYPTED' in f.read()
    _load(priv, password=b'pw-abc')  # decryptable with the passphrase


def test_existing_plaintext_key_gets_encrypted(crypto):
    priv, pub, _ = crypto.generate_ssh_key_pair()  # unencrypted first
    priv2, pub2, eff = crypto.generate_ssh_key_pair(passphrase='pw-2')
    assert priv2 == priv and eff == 'pw-2'
    _load(priv, password=b'pw-2')


def test_encrypted_key_not_re_encrypted(crypto):
    priv, pub, _ = crypto.generate_ssh_key_pair(passphrase='pw-orig')
    before = open(priv, 'rb').read()
    priv2, pub2, eff = crypto.generate_ssh_key_pair(passphrase='pw-other')
    assert eff is None  # must not claim a mismatched passphrase
    assert open(priv, 'rb').read() == before


def test_encrypt_in_place(crypto):
    priv, pub, _ = crypto.generate_ssh_key_pair()
    assert crypto.encrypt_ssh_private_key(priv, 'pw-x') is True
    assert crypto.encrypt_ssh_private_key(priv, 'pw-y') is False  # already encrypted
    _load(priv, password=b'pw-x')
