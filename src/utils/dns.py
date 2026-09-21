"""Optional Cloudflare DNS synchronization.

When a Cloudflare API token is configured in Settings, saving a tunnel
ensures an A record exists for its hostname pointing at the selected
server's IP. Everything is opt-in: with no token configured none of this
code runs and DNS stays manual (or wildcard) exactly as before.

Only creates missing records — an existing record is never modified,
since the user may have pointed it elsewhere deliberately.
"""
import logging

import requests

CF_API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareClient:
    """Minimal Cloudflare v4 API client for A-record management."""

    def __init__(self, api_token: str, session: requests.Session = None):
        self.session = session or requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {api_token}',
            'Content-Type': 'application/json',
        })

    def _get(self, path: str, **params):
        resp = self.session.get(f"{CF_API_BASE}{path}", params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict):
        resp = self.session.post(f"{CF_API_BASE}{path}", json=payload, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _find_zone_id(self, hostname: str) -> str | None:
        """Returns the ID of the zone that is the longest suffix match for
        the hostname ('*.lab.example.com' and 'a.b.example.com' both match
        the 'example.com' zone)."""
        name = hostname.lstrip('*.')
        zones = self._get('/zones', per_page=50).get('result') or []
        best = None
        for zone in zones:
            zname = zone.get('name', '')
            if name == zname or name.endswith('.' + zname):
                if best is None or len(zname) > len(best['name']):
                    best = zone
        return best['id'] if best else None

    def ensure_a_record(self, hostname: str, ip: str) -> tuple[bool, str]:
        """Creates an A record for hostname -> ip if one doesn't exist.

        Wildcard names ('*.lab.example.com') are valid Cloudflare records
        and are created as-is. Existing records are left untouched.

        Returns (success, human-readable message).
        """
        zone_id = self._find_zone_id(hostname)
        if not zone_id:
            return False, f"no Cloudflare zone matches '{hostname}'"

        existing = self._get(f'/zones/{zone_id}/dns_records',
                             type='A', name=hostname).get('result') or []
        for record in existing:
            if record.get('content') == ip:
                return True, f"A record for {hostname} already points to {ip}"
        if existing:
            current = existing[0].get('content', '?')
            return True, (f"A record for {hostname} already exists "
                          f"({current}); leaving it unchanged")

        result = self._post(f'/zones/{zone_id}/dns_records', {
            'type': 'A',
            'name': hostname,
            'content': ip,
            'ttl': 300,
            'proxied': False,  # DNS-only: NerazimNet's Nginx terminates TLS
        })
        if result.get('success'):
            return True, f"created A record {hostname} -> {ip}"
        errors = result.get('errors') or []
        detail = errors[0].get('message', 'unknown error') if errors else 'unknown error'
        return False, f"Cloudflare rejected the record: {detail}"


def sync_tunnel_dns(api_token: str, hostnames: list[str], server_ip: str) -> list[str]:
    """Best-effort A-record sync for a batch of hostnames.

    Never raises — returns one log line per hostname so a DNS hiccup can't
    break route sync. Empty token or empty hostnames is a no-op.
    """
    if not api_token or not hostnames:
        return []
    client = CloudflareClient(api_token)
    logs = []
    for hostname in hostnames:
        try:
            ok, msg = client.ensure_a_record(hostname, server_ip)
        except requests.RequestException as e:
            ok, msg = False, f"Cloudflare API error: {e}"
        logs.append(f"{'✅' if ok else '⚠️'} DNS [{hostname}]: {msg}")
        if not ok:
            logging.warning(f"Cloudflare DNS sync failed for {hostname}: {msg}")
    return logs
