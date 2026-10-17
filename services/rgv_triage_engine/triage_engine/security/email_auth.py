"""SPF and DKIM verification for inbound e-mail.

Facturas and BOLs arriving by e-mail are only trusted if the sending domain
proves it sent them:

* **SPF** (RFC 7208) checks that the connecting MTA's IP is authorised to
  send for the envelope ``MAIL FROM`` domain.
* **DKIM** (RFC 6376) checks a cryptographic signature over the headers and
  body. With ``require_aligned_dkim`` the signing domain must also align with
  the visible ``From:`` domain (relaxed alignment, as in DMARC), so a spammer
  cannot sign with their own domain while spoofing a customer's ``From:``.

Both checks do DNS lookups and are run in a worker thread with timeouts.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import getaddresses, parseaddr
from enum import StrEnum

import anyio
import dkim
import dns.exception
import dns.resolver
import spf


class AuthResult(StrEnum):
    """Normalised SPF/DKIM outcomes."""

    PASS = "pass"  # noqa: S105 - RFC 8601 result keyword, not a password
    FAIL = "fail"
    SOFTFAIL = "softfail"
    NEUTRAL = "neutral"
    NONE = "none"
    TEMPERROR = "temperror"
    PERMERROR = "permerror"


@dataclass(frozen=True, slots=True)
class EnvelopeInfo:
    """SMTP envelope data supplied by the authenticated relay."""

    client_ip: str
    mail_from: str
    helo: str


@dataclass(frozen=True, slots=True)
class EmailAuthVerdict:
    """Outcome of sender authentication."""

    spf: AuthResult
    dkim: AuthResult
    dkim_domain: str | None
    from_domain: str | None
    aligned: bool
    accepted: bool
    reason: str


class EmailAuthenticationError(Exception):
    """The message failed sender authentication policy."""

    def __init__(self, verdict: EmailAuthVerdict) -> None:
        super().__init__(verdict.reason)
        self.verdict = verdict


def _organizational_domain(domain: str) -> str:
    """Approximate organisational domain (last two labels; three for ccSLDs).

    Handles common Mexican/US second-level zones such as ``com.mx``. For full
    accuracy deploy with the Public Suffix List.
    """
    labels = domain.lower().rstrip(".").split(".")
    if len(labels) >= 3 and labels[-2] in {"com", "org", "net", "gob", "edu", "co"}:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _aligned(dkim_domain: str | None, from_domain: str | None) -> bool:
    if not dkim_domain or not from_domain:
        return False
    return _organizational_domain(dkim_domain) == _organizational_domain(from_domain)


def from_header_domain(raw_message: bytes) -> str | None:
    """Return the domain of the single ``From:`` address, or None if ambiguous."""
    header_block = raw_message.split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0]
    from_values = []
    current: list[str] = []
    for line in header_block.decode("utf-8", errors="replace").splitlines():
        if line[:1] in {" ", "\t"} and current:
            current.append(line.strip())
            continue
        if current:
            from_values.append(" ".join(current))
            current = []
        if line.lower().startswith("from:"):
            current = [line[5:].strip()]
    if current:
        from_values.append(" ".join(current))
    # RFC 5322 allows one From: header; multiple are a classic spoofing trick.
    if len(from_values) != 1:
        return None
    addresses = getaddresses(from_values)
    if len(addresses) != 1 or "@" not in addresses[0][1]:
        return None
    return addresses[0][1].rsplit("@", 1)[1].lower()


class EmailAuthenticator:
    """Runs SPF and DKIM checks and applies acceptance policy.

    Args:
        require_spf_pass: Reject unless SPF returns ``pass``.
        require_aligned_dkim: Reject unless at least one DKIM signature
            verifies *and* its ``d=`` domain aligns with ``From:``.
        dns_timeout: Per-check DNS time budget in seconds.
        dkim_txt_lookup: Optional TXT resolver for DKIM keys (tests inject one).
    """

    def __init__(
        self,
        *,
        require_spf_pass: bool,
        require_aligned_dkim: bool,
        dns_timeout: float,
        dkim_txt_lookup: Callable[[bytes], bytes | None] | None = None,
    ) -> None:
        self.require_spf_pass = require_spf_pass
        self.require_aligned_dkim = require_aligned_dkim
        self.dns_timeout = dns_timeout
        self._dkim_txt_lookup = dkim_txt_lookup or self._resolve_txt

    async def verify(self, raw_message: bytes, envelope: EnvelopeInfo) -> EmailAuthVerdict:
        """Authenticate a message; return a verdict (does not raise on failure)."""
        with anyio.fail_after(self.dns_timeout * 3):
            spf_result = await anyio.to_thread.run_sync(self._check_spf, envelope)
            dkim_result, dkim_domain = await anyio.to_thread.run_sync(self._check_dkim, raw_message)
        from_domain = from_header_domain(raw_message)
        aligned = dkim_result is AuthResult.PASS and _aligned(dkim_domain, from_domain)

        reasons = []
        if from_domain is None:
            reasons.append("missing, multiple or malformed From header")
        if self.require_spf_pass and spf_result is not AuthResult.PASS:
            reasons.append(f"SPF {spf_result.value}")
        if self.require_aligned_dkim and not aligned:
            reasons.append(f"DKIM {dkim_result.value} (aligned={aligned})")
        accepted = not reasons
        return EmailAuthVerdict(
            spf=spf_result,
            dkim=dkim_result,
            dkim_domain=dkim_domain,
            from_domain=from_domain,
            aligned=aligned,
            accepted=accepted,
            reason="accepted" if accepted else "; ".join(reasons),
        )

    async def enforce(self, raw_message: bytes, envelope: EnvelopeInfo) -> EmailAuthVerdict:
        """Like :meth:`verify` but raise :class:`EmailAuthenticationError` on rejection."""
        verdict = await self.verify(raw_message, envelope)
        if not verdict.accepted:
            raise EmailAuthenticationError(verdict)
        return verdict

    def _check_spf(self, envelope: EnvelopeInfo) -> AuthResult:
        try:
            ipaddress.ip_address(envelope.client_ip)
        except ValueError:
            return AuthResult.PERMERROR
        _, address = parseaddr(envelope.mail_from)
        if "@" not in address:
            return AuthResult.PERMERROR
        result, _explanation = spf.check2(
            i=envelope.client_ip,
            s=address,
            h=envelope.helo,
            timeout=self.dns_timeout,
            querytime=self.dns_timeout,
        )
        try:
            return AuthResult(result)
        except ValueError:
            return AuthResult.PERMERROR

    def _resolve_txt(self, name: bytes) -> bytes | None:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = self.dns_timeout
        try:
            answer = resolver.resolve(name.decode(), "TXT")
        except (dns.exception.DNSException, UnicodeDecodeError):
            return None
        return b"".join(b"".join(rdata.strings) for rdata in answer)

    def _check_dkim(self, raw_message: bytes) -> tuple[AuthResult, str | None]:
        def dnsfunc(name: bytes, timeout: int = 5) -> bytes | None:
            return self._dkim_txt_lookup(name)

        try:
            verifier = dkim.DKIM(raw_message, minkey=1024)
            signature_count = sum(
                1 for name, _ in verifier.headers if name.lower() == b"dkim-signature"
            )
            if signature_count == 0:
                return AuthResult.NONE, None
            fallback_domain: str | None = None
            from_domain = from_header_domain(raw_message)
            # Accept the first valid signature from an aligned domain; a
            # third-party (e.g. ESP) signature alone is recorded but unaligned.
            for idx in range(min(signature_count, 5)):
                try:
                    valid = verifier.verify(idx=idx, dnsfunc=dnsfunc)
                except dkim.ValidationError:
                    valid = False  # e.g. body hash mismatch: a failed signature
                if valid:
                    domain = verifier.domain.decode() if verifier.domain else None
                    if _aligned(domain, from_domain):
                        return AuthResult.PASS, domain
                    fallback_domain = fallback_domain or domain
            if fallback_domain:
                return AuthResult.PASS, fallback_domain
            return AuthResult.FAIL, None
        except dkim.DKIMException:
            return AuthResult.PERMERROR, None
