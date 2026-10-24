"""HttpFetcher: fetches a JD page and extracts its main text.

Only http(s); every host, including each redirect target, must resolve to public
addresses, so the fetcher is safe to run on a server later."""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx
import trafilatura

from cvopt import __version__
from cvopt.config import FetchSettings
from cvopt.errors import InputError
from cvopt.ports import FetchedPage

Resolver = Callable[[str], Awaitable[list[str]]]
MAX_REDIRECTS = 5
ASK = "Download it as a PDF or paste the text instead (--pdf or --text)."


async def system_resolver(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


def _fail(url: str, why: str) -> InputError:
    return InputError(f"Could not read {url}: {why}. {ASK}")


class HttpFetcher:
    def __init__(
        self,
        s: FetchSettings,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._s = s
        self._resolve = resolver
        self._transport = transport

    async def _check_host(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise _fail(url, "only http and https URLs are supported")
        try:
            addresses = await self._resolve(parts.hostname)
        except OSError as err:
            raise _fail(url, f"the host cannot be resolved ({err})") from err
        for addr in addresses:
            ip = ipaddress.ip_address(addr)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise _fail(url, "it points to a private or local address")

    async def fetch_main_text(self, url: str) -> FetchedPage:
        headers = {"User-Agent": f"cvopt/{__version__}"}
        async with httpx.AsyncClient(
            timeout=self._s.timeout_s, headers=headers, transport=self._transport
        ) as client:
            current = url
            for _ in range(MAX_REDIRECTS + 1):
                await self._check_host(current)
                try:
                    async with client.stream("GET", current) as resp:
                        if resp.is_redirect and "location" in resp.headers:
                            current = urljoin(current, resp.headers["location"])
                            continue
                        if resp.status_code >= 400:
                            raise _fail(url, f"the server answered {resp.status_code}")
                        body = await self._read(resp)
                        media_type = resp.headers.get("content-type", "").split(";")[0].strip()
                        break
                except httpx.HTTPError as err:
                    raise _fail(url, f"the request failed ({type(err).__name__})") from err
            else:
                raise _fail(url, "too many redirects")
        if media_type == "application/pdf":
            return FetchedPage(current, media_type, "", "", body)
        if media_type not in ("text/html", "application/xhtml+xml"):
            raise _fail(url, f"the page is {media_type or 'of unknown type'}, not HTML or PDF")
        html = body.decode(resp.encoding or "utf-8", errors="replace")
        text = (
            trafilatura.extract(
                html, url=current, include_tables=True, include_comments=False, favor_recall=True
            )
            or ""
        )
        if len(text) < self._s.min_main_text_chars:
            raise _fail(url, "the page has too little text (a login wall or a script-only page?)")
        meta = trafilatura.extract_metadata(html, default_url=current)
        title = (meta.title if meta and meta.title else "") or ""
        return FetchedPage(current, "text/html", title, text, body)

    async def _read(self, resp: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in resp.aiter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size >= self._s.max_bytes:
                break
        return b"".join(chunks)[: self._s.max_bytes]
