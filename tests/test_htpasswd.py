"""Unit tests for the pure-Python apr1-md5 htpasswd implementation."""
from utils.htpasswd import apr1_md5, htpasswd_line


class TestApr1Md5:
    # Expected digests generated with `openssl passwd -apr1 -salt <salt> <pw>`
    def test_matches_openssl_vectors(self):
        assert apr1_md5('password', 'hfT7jp2q') == '$apr1$hfT7jp2q$two3QJlp/Qr/L8kifGFHF1'
        assert apr1_md5('my secret', 'saltsalt') == '$apr1$saltsalt$b6pwSIrx5K2SLL0ITxyNm/'
        assert apr1_md5('hello world', 'abcd1234') == '$apr1$abcd1234$IDFuCyp6kQBpBtsflxqXh1'

    def test_htpasswd_line_format(self):
        line = htpasswd_line('admin', 'pw', salt='saltsalt')
        assert line == 'admin:$apr1$saltsalt$x78Y39ym2RjUNQHTLgwHz/'

    def test_random_salt_produces_valid_line(self):
        line = htpasswd_line('admin', 'pw')
        user, digest = line.split(':', 1)
        assert user == 'admin'
        assert digest.startswith('$apr1$')
