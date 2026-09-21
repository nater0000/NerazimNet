"""Unit tests for the opt-in Cloudflare DNS sync (utils/dns.py)."""
import requests

from utils.dns import CloudflareClient, sync_tunnel_dns


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    """Records GET/POST calls and serves canned API payloads."""

    def __init__(self, zones=None, records=None, post_result=None):
        self.headers = {}
        self.zones = zones or []
        self.records = records or []
        self.post_result = post_result or {'success': True}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(('GET', url, params))
        if url.endswith('/zones'):
            return FakeResponse({'result': self.zones})
        return FakeResponse({'result': self.records})

    def post(self, url, json=None, timeout=None):
        self.calls.append(('POST', url, json))
        return FakeResponse(self.post_result)


def _client(**kw):
    return CloudflareClient('test-token', session=FakeSession(**kw))


class TestFindZone:
    def test_matches_apex_zone(self):
        c = _client(zones=[{'id': 'z1', 'name': 'example.com'}])
        assert c._find_zone_id('app.example.com') == 'z1'

    def test_wildcard_stripped_for_zone_lookup(self):
        c = _client(zones=[{'id': 'z1', 'name': 'example.com'}])
        assert c._find_zone_id('*.lab.example.com') == 'z1'

    def test_longest_suffix_wins(self):
        c = _client(zones=[{'id': 'z1', 'name': 'example.com'},
                           {'id': 'z2', 'name': 'lab.example.com'}])
        assert c._find_zone_id('app.lab.example.com') == 'z2'

    def test_no_zone_returns_none(self):
        c = _client(zones=[{'id': 'z1', 'name': 'other.com'}])
        assert c._find_zone_id('app.example.com') is None


class TestEnsureARecord:
    ZONES = [{'id': 'z1', 'name': 'example.com'}]

    def test_creates_missing_record(self):
        c = _client(zones=self.ZONES, records=[])
        ok, msg = c.ensure_a_record('app.example.com', '1.2.3.4')
        assert ok
        post = next(call for call in c.session.calls if call[0] == 'POST')
        payload = post[2]
        assert payload == {'type': 'A', 'name': 'app.example.com',
                           'content': '1.2.3.4', 'ttl': 300, 'proxied': False}

    def test_creates_wildcard_record_as_is(self):
        c = _client(zones=self.ZONES, records=[])
        ok, _ = c.ensure_a_record('*.lab.example.com', '1.2.3.4')
        assert ok
        post = next(call for call in c.session.calls if call[0] == 'POST')
        assert post[2]['name'] == '*.lab.example.com'

    def test_existing_matching_record_is_noop(self):
        c = _client(zones=self.ZONES,
                    records=[{'content': '1.2.3.4'}])
        ok, msg = c.ensure_a_record('app.example.com', '1.2.3.4')
        assert ok and 'already points' in msg
        assert not any(call[0] == 'POST' for call in c.session.calls)

    def test_existing_different_record_left_alone(self):
        c = _client(zones=self.ZONES,
                    records=[{'content': '9.9.9.9'}])
        ok, msg = c.ensure_a_record('app.example.com', '1.2.3.4')
        assert ok and 'leaving it unchanged' in msg
        assert not any(call[0] == 'POST' for call in c.session.calls)

    def test_no_matching_zone_fails(self):
        c = _client(zones=[{'id': 'z1', 'name': 'other.com'}])
        ok, msg = c.ensure_a_record('app.example.com', '1.2.3.4')
        assert not ok and 'no Cloudflare zone' in msg


class TestSyncTunnelDns:
    def test_no_token_is_noop(self):
        assert sync_tunnel_dns('', ['app.example.com'], '1.2.3.4') == []

    def test_empty_hostnames_is_noop(self):
        assert sync_tunnel_dns('tok', [], '1.2.3.4') == []

    def test_never_raises_on_api_error(self, monkeypatch):
        class BoomSession(FakeSession):
            def get(self, *a, **kw):
                raise requests.ConnectionError('no network')
        monkeypatch.setattr('utils.dns.CloudflareClient',
                            lambda token: CloudflareClient(token, session=BoomSession()))
        logs = sync_tunnel_dns('tok', ['app.example.com'], '1.2.3.4')
        assert len(logs) == 1
        assert '⚠️' in logs[0] and 'Cloudflare API error' in logs[0]
