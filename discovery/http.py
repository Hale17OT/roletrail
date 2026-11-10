"""Bounded public HTTP client: caching, retries, rate limits and visible failures."""
import ipaddress
import json
import socket
import time
from urllib.parse import urljoin, urlsplit

import requests


class BudgetExceeded(RuntimeError):
    pass


class Fetcher:
    def __init__(self, store=None, budget=250, timeout=15):
        self.store, self.budget, self.timeout = store, budget, timeout
        self.requests = 0
        self.cache, self.last_host = {}, {}
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "RoletrailJobDiscovery/1.0 (personal job search)"

    @staticmethod
    def public_url(url):
        p = urlsplit(url)
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password or p.port not in {None, 80, 443}:
            raise ValueError("Only public HTTP(S) sources are allowed")
        addresses = socket.getaddrinfo(p.hostname, p.port or 443, type=socket.SOCK_STREAM)
        if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError("Private network source blocked")

    def get(self, url, fresh=False, ttl=3600):
        cached = self.cache.get(url)
        if not fresh and not cached and self.store:
            cached = self.store.get("http:" + url)
        if not fresh and cached and time.time() - cached["cached_at"] < ttl:
            return cached
        current = url
        for hop in range(6):
            self.public_url(current)
            for attempt in range(3):
                if self.requests >= self.budget:
                    raise BudgetExceeded("HTTP request budget reached; coverage incomplete")
                host = urlsplit(current).hostname
                delay = .3 - (time.monotonic() - self.last_host.get(host, 0))
                if delay > 0:
                    time.sleep(delay)
                self.requests += 1
                self.last_host[host] = time.monotonic()
                response = self.session.get(current, timeout=self.timeout, allow_redirects=False, stream=True)
                if response.status_code in {429, 500, 502, 503, 504}:
                    retry = response.headers.get("Retry-After", "")
                    response.close()
                    if retry.isdigit() and int(retry) > 30:
                        raise RuntimeError(f"Source throttled; retry after {retry} seconds")
                    if attempt == 2:
                        raise RuntimeError("Source unavailable after retries")
                    time.sleep(int(retry) if retry.isdigit() else 2 ** attempt)
                    continue
                break
            if response.is_redirect:
                current = urljoin(current, response.headers["Location"])
                response.close()
                continue
            try:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    if size > 8_000_000:
                        raise ValueError("Source response too large")
                    chunks.append(chunk)
                body = b"".join(chunks).decode(response.encoding if response.encoding and response.encoding != "ISO-8859-1" else "utf-8", errors="replace")
                result = {"url": current, "text": body, "status": response.status_code,
                          "cached_at": time.time(), "content_type": response.headers.get("Content-Type", "")}
            finally:
                response.close()
            self.cache[url] = result
            if self.store:
                self.store.set("http:" + url, result)
            return result
        raise ValueError("Too many redirects")

    def json(self, url, **kwargs):
        return json.loads(self.get(url, **kwargs)["text"])
