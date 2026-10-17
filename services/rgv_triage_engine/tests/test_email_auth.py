"""SPF / DKIM policy with real DKIM signatures and stubbed DNS."""

from __future__ import annotations

import base64
import shutil
import subprocess
from pathlib import Path
from typing import Any

import anyio
import dkim
import pytest

from triage_engine.security import email_auth
from triage_engine.security.email_auth import (
    AuthResult,
    EmailAuthenticator,
    EnvelopeInfo,
    from_header_domain,
)

ENVELOPE = EnvelopeInfo(
    client_ip="203.0.113.10", mail_from="bounce@supplier.com.mx", helo="mx.supplier.com.mx"
)


@pytest.fixture(scope="module")
def rsa_key(tmp_path_factory: pytest.TempPathFactory) -> tuple[bytes, bytes]:
    """Generate a throwaway RSA key pair with openssl (PKCS#1 private key)."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl not available")
    directory: Path = tmp_path_factory.mktemp("dkim")
    private = directory / "key.pem"
    subprocess.run(
        ["openssl", "genrsa", "-traditional", "-out", str(private), "2048"],
        check=True,
        capture_output=True,
    )
    public_der = subprocess.run(
        ["openssl", "rsa", "-in", str(private), "-pubout", "-outform", "DER"],
        check=True,
        capture_output=True,
    ).stdout
    return private.read_bytes(), base64.b64encode(public_der)


def _message(from_header: str = "Facturas <facturas@supplier.com.mx>") -> bytes:
    return (
        f"From: {from_header}\r\n"
        "To: docs@tenant.example.com\r\n"
        "Subject: Factura\r\n"
        "Message-ID: <1@supplier.com.mx>\r\n"
        "\r\n"
        "Adjuntamos factura.\r\n"
    ).encode()


def _sign(message: bytes, key: bytes, domain: bytes) -> bytes:
    signature = dkim.sign(
        message, b"sel1", domain, key, include_headers=[b"from", b"to", b"subject"]
    )
    return signature + message


def _authenticator(public_key: bytes, **kwargs: Any) -> EmailAuthenticator:
    record = b"v=DKIM1; k=rsa; p=" + public_key

    def lookup(name: bytes) -> bytes | None:
        return record if name.startswith(b"sel1._domainkey.") else None

    defaults: dict[str, Any] = {
        "require_spf_pass": True,
        "require_aligned_dkim": True,
        "dns_timeout": 2.0,
    }
    defaults.update(kwargs)
    return EmailAuthenticator(dkim_txt_lookup=lookup, **defaults)


@pytest.fixture
def spf_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_auth.spf, "check2", lambda **_: ("pass", "ok"))


def test_aligned_dkim_and_spf_pass_accepted(rsa_key: tuple[bytes, bytes], spf_pass: None) -> None:
    private, public = rsa_key
    raw = _sign(_message(), private, b"supplier.com.mx")
    verdict = anyio.run(_authenticator(public).verify, raw, ENVELOPE)
    assert verdict.accepted, verdict.reason
    assert verdict.dkim is AuthResult.PASS and verdict.aligned


def test_subdomain_signature_aligns_relaxed(rsa_key: tuple[bytes, bytes], spf_pass: None) -> None:
    private, public = rsa_key
    raw = _sign(_message(), private, b"mail.supplier.com.mx")
    assert anyio.run(_authenticator(public).verify, raw, ENVELOPE).aligned


def test_unaligned_signature_rejected(rsa_key: tuple[bytes, bytes], spf_pass: None) -> None:
    private, public = rsa_key
    raw = _sign(_message(), private, b"attacker.example")
    verdict = anyio.run(_authenticator(public).verify, raw, ENVELOPE)
    assert verdict.dkim is AuthResult.PASS and not verdict.aligned and not verdict.accepted


def test_modified_body_fails_dkim(rsa_key: tuple[bytes, bytes], spf_pass: None) -> None:
    private, public = rsa_key
    raw = _sign(_message(), private, b"supplier.com.mx").replace(b"Adjuntamos", b"Modificamos")
    verdict = anyio.run(_authenticator(public).verify, raw, ENVELOPE)
    assert verdict.dkim is AuthResult.FAIL and not verdict.accepted


def test_unsigned_message_rejected(rsa_key: tuple[bytes, bytes], spf_pass: None) -> None:
    verdict = anyio.run(_authenticator(rsa_key[1]).verify, _message(), ENVELOPE)
    assert verdict.dkim is AuthResult.NONE and not verdict.accepted


def test_spf_fail_rejected(rsa_key: tuple[bytes, bytes], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_auth.spf, "check2", lambda **_: ("fail", "not permitted"))
    private, public = rsa_key
    raw = _sign(_message(), private, b"supplier.com.mx")
    verdict = anyio.run(_authenticator(public).verify, raw, ENVELOPE)
    assert verdict.spf is AuthResult.FAIL and not verdict.accepted
    assert anyio.run(_authenticator(public, require_spf_pass=False).verify, raw, ENVELOPE).accepted


def test_invalid_envelope_ip_is_permerror(rsa_key: tuple[bytes, bytes]) -> None:
    bad = EnvelopeInfo(client_ip="not-an-ip", mail_from="a@b.mx", helo="x")
    verdict = anyio.run(_authenticator(rsa_key[1]).verify, _message(), bad)
    assert verdict.spf is AuthResult.PERMERROR


def test_multiple_from_headers_are_ambiguous() -> None:
    raw = b"From: a@supplier.com.mx\r\nFrom: b@attacker.example\r\n\r\nbody"
    assert from_header_domain(raw) is None
    assert from_header_domain(_message()) == "supplier.com.mx"
