import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from controllers.config_manager import ConfigManager


class FakeCtrl:
    def __init__(self):
        self.divergence_calls = []

    def get_my_device_id(self):
        return "device-self"

    def get_my_device_name(self):
        return "Self"

    def notify_config_divergence(self, names):
        self.divergence_calls.append(list(names))


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(
        'controllers.config_manager.get_app_data_dir', lambda: str(tmp_path))
    monkeypatch.setattr(
        'utils.crypto.get_app_data_dir', lambda: str(tmp_path))
    ctrl = FakeCtrl()
    cm = ConfigManager(ctrl)
    ok, rk = cm.unlock_with_password("test-pw")
    assert ok
    return cm, ctrl, rk


def _write_divergent_patch(cm, file_id, ts="2999-01-01T00-00-00.000000Z"):
    """A patch authored against a different base (another device's fork)."""
    patches = cm.dmp.patch_make("UNIQUE-CONTEXT-NEVER-PRESENT" * 20, "replaced")
    patch_text = cm.dmp.patch_toText(patches)
    enc = cm.crypto_manager.encrypt_data(patch_text.encode('utf-8'), "test-pw")
    path = os.path.join(cm.sync_path, f"{ts}_{file_id}.patch")
    with open(path, 'wb') as f:
        f.write(enc)
    return path


def test_clean_chain_reconstructs(config):
    cm, *_ = config
    oid = cm.add_object('tunnel', {'hostname': 'a.example.com', 'port': 443})
    data = dict(cm.get_object_by_id(oid))
    data['port'] = 8443
    cm.update_object(oid, data)

    cm.load_configuration()
    obj = cm.get_object_by_id(oid)
    assert obj['port'] == 8443
    assert cm.get_divergent_files() == {}


def test_divergent_patch_truncates_and_flags(config):
    cm, ctrl, _ = config
    oid = cm.add_object('tunnel', {'hostname': 'a.example.com', 'port': 443})
    before = dict(cm.get_object_by_id(oid))

    _write_divergent_patch(cm, oid)
    cm.load_configuration()

    # Truncated at the fork: object still parses to pre-divergence content
    assert cm.get_object_by_id(oid) == before
    # Divergence is flagged and the controller was notified once
    assert oid in cm.get_divergent_files()
    assert len(ctrl.divergence_calls) == 1
    # Second reload does not re-notify
    cm.load_configuration()
    assert len(ctrl.divergence_calls) == 1


def test_divergence_does_not_corrupt_other_files(config):
    cm, *_ = config
    oid_bad = cm.add_object('tunnel', {'hostname': 'bad.example.com'})
    oid_ok = cm.add_object('server', {'name': 'vps', 'ip_address': '1.2.3.4'})

    _write_divergent_patch(cm, oid_bad)
    cm.load_configuration()

    assert cm.get_object_by_id(oid_ok)['ip_address'] == '1.2.3.4'
    assert list(cm.get_divergent_files()) == [oid_bad]


def test_ensure_ssh_key_encrypted_migrates(config):
    cm, *_ = config
    priv, pub, _ = cm.crypto_manager.generate_ssh_key_pair()  # plaintext key
    cm.save_or_update_automation_credentials(priv, pub)

    cm.ensure_ssh_key_encrypted()

    creds = cm.get_automation_credentials()
    assert creds['ssh_private_key_passphrase']
    with open(priv, 'rb') as f:
        assert b'ENCRYPTED' in f.read()
    # idempotent: second run must not rotate the stored passphrase
    pw1 = creds['ssh_private_key_passphrase']
    cm.ensure_ssh_key_encrypted()
    assert cm.get_automation_credentials()['ssh_private_key_passphrase'] == pw1


def test_ensure_ssh_key_encrypted_skips_external_key(config, tmp_path):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    cm, *_ = config
    key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend())
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption())
    ext = tmp_path / 'external.pem'
    ext.write_bytes(pem)
    cm.save_or_update_automation_credentials(str(ext), '')

    cm.ensure_ssh_key_encrypted()

    assert ext.read_bytes() == pem  # user's own key is never rewritten
    assert 'ssh_private_key_passphrase' not in cm.get_automation_credentials()


def test_content_at_version_uses_clean_prefix(config):
    cm, *_ = config
    oid = cm.add_object('tunnel', {'hostname': 'hist.example.com'})
    versions = cm.get_file_version_history(oid)
    assert versions
    _write_divergent_patch(cm, oid)
    content = cm.get_file_content_at_version(oid, versions[-1]['timestamp'])
    assert json.loads(content)['hostname'] == 'hist.example.com'


def test_recovery_key_unwraps_master_password(config):
    cm, _, rk = config
    assert rk
    assert cm.recover_with_recovery_key(rk) == "test-pw"
    assert cm.recover_with_recovery_key("wrong-key") is None


def test_change_password_reencrypts_and_preserves_recovery(config):
    cm, _, rk = config
    oid = cm.add_object('tunnel', {'hostname': 'keep.example.com'})

    assert cm.re_encrypt_with_new_password("test-pw", "new-pw")
    ok, _ = cm.unlock_with_password("new-pw")
    assert ok
    assert cm.get_object_by_id(oid)['hostname'] == 'keep.example.com'
    # recovery artifacts refreshed under the new password
    assert cm.recover_with_recovery_key(rk) == "new-pw"
    assert cm.get_recovery_key() == rk


def test_recovery_reset_flow(config):
    cm, _, rk = config
    oid = cm.add_object('tunnel', {'hostname': 'survives.example.com'})
    master = cm.recover_with_recovery_key(rk)
    assert master == "test-pw"
    assert cm.re_encrypt_with_new_password(master, "reset-pw", recovery_key=rk)
    ok, _ = cm.unlock_with_password("reset-pw")
    assert ok
    assert cm.get_object_by_id(oid)['hostname'] == 'survives.example.com'


def test_prune_squashes_patch_history(config):
    cm, *_ = config
    oid = cm.add_object('tunnel', {'hostname': 'squash.example.com', 'n': 0})
    data = dict(cm.get_object_by_id(oid))
    for i in range(1, 26):
        data['n'] = i
        cm.update_object(oid, dict(data))

    cm._wait_for_prunes()  # pruning runs in a worker thread
    patches = sorted(f for f in os.listdir(cm.sync_path)
                     if f.endswith(f'_{oid}.patch'))
    assert len(patches) <= cm.PATCH_KEEP
    # squash kept the chain intact — full state reconstructs
    cm.load_configuration()
    assert cm.get_object_by_id(oid)['n'] == 25
