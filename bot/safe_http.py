"""Bounded public HTTP reads with DNS pinning and per-redirect validation."""
import ipaddress
import json
import socket
from urllib.parse import urlsplit, urljoin

import urllib3


class Response:
    def __init__(self, status, content):
        self.status_code, self.content = status, content

    def json(self):
        return json.loads(self.content)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError(f"HTTP {self.status_code}")


def public_target(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Yalnız genel HTTP(S) adresleri okunabilir.")
    if parsed.port not in (None, 80, 443):
        raise ValueError("Bu bağlantı noktası desteklenmiyor.")
    records = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(row[4][0] for row in records))
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("İç ağ, yerel ve metadata adresleri okunamaz.")
    return parsed, addresses[0]


def fetch_public(url, headers=None, timeout=10, max_bytes=2_000_000):
    for _ in range(4):
        parsed, ip = public_target(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        pool_cls = urllib3.HTTPSConnectionPool if parsed.scheme == "https" else urllib3.HTTPConnectionPool
        kwargs = {"server_hostname": parsed.hostname, "assert_hostname": parsed.hostname} if parsed.scheme == "https" else {}
        pool = pool_cls(ip, port=port, timeout=urllib3.Timeout(connect=5, read=timeout), **kwargs)
        response = None
        try:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            response = pool.urlopen("GET", path, headers={**(headers or {}), "Host": parsed.netloc},
                                    redirect=False, retries=False, preload_content=False)
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("Geçersiz yönlendirme.")
                url = urljoin(url, location)
                continue
            content = response.read(max_bytes + 1, decode_content=True)
            if len(content) > max_bytes:
                raise ValueError("Sayfa indirme boyutu sınırı aşıldı.")
            return Response(response.status, content)
        finally:
            if response is not None:
                response.close()
            pool.close()
    raise ValueError("Çok fazla yönlendirme.")
