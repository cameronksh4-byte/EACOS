"""Download of WhatsApp media attachments through the Graph API."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from triage_engine.config import Settings

ALLOWED_MEDIA_TYPES = frozenset(
    {"application/pdf", "image/jpeg", "image/png", "image/webp", "application/xml", "text/xml"}
)


class MediaDownloadError(Exception):
    """The attachment could not be fetched safely."""


@dataclass(frozen=True, slots=True)
class DownloadedMedia:
    """Attachment bytes and their verified metadata."""

    content: bytes
    mime_type: str
    sha256: str


class WhatsAppMediaClient:
    """Fetches attachments with SSRF, size and integrity controls.

    * The CDN URL returned by the Graph API must be HTTPS and on an
      allow-listed host, so a tampered response cannot point the service at
      internal addresses (e.g. the EC2 metadata endpoint).
    * Redirects are not followed.
    * Downloads stream and abort as soon as ``max_bytes`` is exceeded.
    * The content hash is checked against the SHA-256 Meta reports.

    Args:
        settings: Application settings.
        http: Optional shared ``httpx.AsyncClient``.
    """

    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False
        )
        self._max_bytes = settings.max_request_body_bytes

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def download(self, media_id: str) -> DownloadedMedia:
        """Resolve a media ID and download it.

        Raises:
            MediaDownloadError: On any validation or transport failure.
        """
        token = self._settings.require_secret("whatsapp_access_token")
        headers = {"Authorization": f"Bearer {token}"}
        meta_url = (
            f"https://graph.facebook.com/{self._settings.whatsapp_graph_api_version}/{media_id}"
        )
        try:
            meta = await self._http.get(meta_url, headers=headers)
            meta.raise_for_status()
            info = meta.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MediaDownloadError("media lookup failed") from exc

        url = str(info.get("url", ""))
        mime_type = str(info.get("mime_type", "")).split(";", 1)[0].lower()
        declared_size = int(info.get("file_size") or 0)
        if mime_type not in ALLOWED_MEDIA_TYPES:
            raise MediaDownloadError(f"media type {mime_type!r} not accepted")
        if declared_size > self._max_bytes:
            raise MediaDownloadError("media exceeds size limit")
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.hostname not in self._settings.whatsapp_media_host_allowlist
        ):
            raise MediaDownloadError("media URL host not allow-listed")

        chunks: list[bytes] = []
        total = 0
        try:
            async with self._http.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self._max_bytes:
                        raise MediaDownloadError("media exceeds size limit")
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise MediaDownloadError("media download failed") from exc

        content = b"".join(chunks)
        digest = hashlib.sha256(content).hexdigest()
        expected = info.get("sha256")
        if expected and not _sha256_matches(digest, str(expected)):
            raise MediaDownloadError("media checksum mismatch")
        return DownloadedMedia(content=content, mime_type=mime_type, sha256=digest)


def _sha256_matches(hex_digest: str, reported: str) -> bool:
    """Meta reports SHA-256 as hex or base64 depending on API version."""
    candidates = {reported.lower()}
    with contextlib.suppress(ValueError, TypeError):
        candidates.add(base64.b64decode(reported, validate=True).hex())
    return any(hmac.compare_digest(hex_digest, c) for c in candidates)
