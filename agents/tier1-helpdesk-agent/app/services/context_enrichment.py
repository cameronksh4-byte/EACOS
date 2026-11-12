"""Tier 1: live identity and MDM context for the reporter.

Before SOP retrieval, the reporter's email is looked up in the configured
provider (Microsoft Graph for Entra ID + Intune, Okta, or the Supabase
`tenant_directory` cache) and turned into a strict, immutable `UserContext`.
Its `os_platform` becomes a hard metadata filter on the pgvector SOP query.

Rules:
* Lookups never fail a ticket. Errors and timeouts fall back to the cache,
  then to an "unavailable" context, which applies no OS filter.
* Successful live lookups are written through to `tenant_directory`, so the
  cache stays warm for outages and for the cheaper DIRECTORY_CACHE mode.
* The OS platform is chosen deterministically (see `choose_os_platform`).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import httpx

from app.config import Feature, IdentityProviderKind, get_settings
from app.gating import gated
from app.schemas.ticket import HardwareAsset, OSPlatform, UserContext, VisionContext
from app.services import supabase_client

logger = logging.getLogger(__name__)

_EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_http: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=httpx.Timeout(get_settings().identity_lookup_timeout_seconds, connect=3.0))
    return _http


async def aclose() -> None:
    global _http
    if _http is not None:
        await _http.aclose()
        _http = None


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------


def normalise_os(value: str | None) -> OSPlatform:
    v = (value or "").lower()
    if "windows" in v:
        return OSPlatform.WINDOWS
    if "mac" in v or "os x" in v:
        return OSPlatform.MACOS
    if "ios" in v or "ipados" in v or "iphone" in v or "ipad" in v:
        return OSPlatform.IOS
    if "android" in v:
        return OSPlatform.ANDROID
    if "chrome" in v:
        return OSPlatform.CHROMEOS
    if "linux" in v or "ubuntu" in v:
        return OSPlatform.LINUX
    return OSPlatform.UNKNOWN


_DESKTOP = (OSPlatform.WINDOWS, OSPlatform.MACOS, OSPlatform.LINUX, OSPlatform.CHROMEOS)


def choose_os_platform(hardware: list[HardwareAsset], vision_hint: OSPlatform = OSPlatform.UNKNOWN) -> OSPlatform:
    """Deterministic platform for the SOP filter.

    1. The OS seen in the ticket's screenshot, if the user has a device on it
       (or has no devices on record).
    2. The only desktop platform among the user's devices.
    3. The first listed device's platform (providers list the most recent first).
    4. unknown, which applies no filter.
    """
    platforms = [h.os_platform for h in hardware if h.os_platform is not OSPlatform.UNKNOWN]
    if vision_hint is not OSPlatform.UNKNOWN and (not platforms or vision_hint in platforms):
        return vision_hint
    desktops = sorted({p for p in platforms if p in _DESKTOP}, key=lambda p: p.value)
    if len(desktops) == 1:
        return desktops[0]
    return platforms[0] if platforms else OSPlatform.UNKNOWN


def is_vip(job_title: str | None) -> bool:
    title = f" {(job_title or '').lower()} "
    return any(f" {kw} " in title or title.strip().startswith(kw) for kw in get_settings().vip_job_title_keywords)


def _odata_literal(value: str) -> str:
    return value.replace("'", "''")


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class IdentityProvider(Protocol):
    name: str

    async def lookup(self, tenant_id: str, email: str) -> UserContext | None: ...


class DirectoryCacheProvider:
    name = "directory_cache"

    async def lookup(self, tenant_id: str, email: str) -> UserContext | None:
        rows = await supabase_client.select(
            "tenant_directory",
            {"tenant_id": supabase_client.eq(tenant_id), "email": supabase_client.eq(email)},
            limit=1,
        )
        if not rows:
            return None
        row = rows[0]
        hardware = [HardwareAsset.model_validate(h) for h in (row.get("assigned_hardware") or [])][:20]
        os_platform = OSPlatform(row.get("os_platform") or "unknown")
        if os_platform is OSPlatform.UNKNOWN:
            os_platform = choose_os_platform(hardware)
        return UserContext(
            email=email,
            os_platform=os_platform,
            department=row.get("department"),
            vip_status=bool(row.get("vip_status")),
            assigned_hardware=hardware,
            recent_auth_failures=int(row.get("recent_auth_failures") or 0),
            manager_email=row.get("manager_email"),
            source="directory_cache",
        )


class MicrosoftGraphProvider:
    """Entra ID user + manager, Intune managed devices, and failed sign-ins (24h).

    App permissions: User.Read.All, DeviceManagementManagedDevices.Read.All,
    and optionally AuditLog.Read.All (sign-in failures; needs Entra ID P1+).
    """

    name = "microsoft_graph"
    GRAPH = "https://graph.microsoft.com/v1.0"

    def __init__(self) -> None:
        self._token: str | None = None
        self._token_expiry = 0.0

    async def _access_token(self) -> str:
        if self._token and time.monotonic() < self._token_expiry - 60:
            return self._token
        s = get_settings()
        resp = await _client().post(
            f"https://login.microsoftonline.com/{s.graph_tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": s.graph_client_id,
                "client_secret": s.graph_client_secret.get_secret_value(),
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.monotonic() + float(data.get("expires_in", 3600))
        return self._token

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any] | None:
        token = await self._access_token()
        resp = await _client().get(f"{self.GRAPH}{path}", params=params, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def lookup(self, tenant_id: str, email: str) -> UserContext | None:
        user = await self._get(f"/users/{email}", {"$select": "id,mail,department,jobTitle"})
        if user is None:
            return None
        lit = _odata_literal(email)
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        manager, devices, signins = await asyncio.gather(
            self._get(f"/users/{email}/manager", {"$select": "mail"}),
            self._get("/deviceManagement/managedDevices", {
                "$filter": f"userPrincipalName eq '{lit}'",
                "$select": "deviceName,model,operatingSystem,osVersion,complianceState,lastSyncDateTime",
                "$top": "20",
            }),
            self._get("/auditLogs/signIns", {
                "$filter": f"userPrincipalName eq '{lit}' and status/errorCode ne 0 and createdDateTime ge {since}",
                "$top": "50",
            }),
            return_exceptions=True,
        )
        device_rows = devices.get("value", []) if isinstance(devices, dict) else []
        device_rows.sort(key=lambda d: d.get("lastSyncDateTime") or "", reverse=True)
        hardware = [
            HardwareAsset(
                device_name=(d.get("deviceName") or "")[:200] or None,
                model=(d.get("model") or "")[:200] or None,
                os_platform=normalise_os(d.get("operatingSystem")),
                os_version=(d.get("osVersion") or "")[:100] or None,
                compliance_state=d.get("complianceState") if d.get("complianceState") in
                ("compliant", "noncompliant") else "unknown",
            )
            for d in device_rows[:20]
        ]
        failures = len(signins.get("value", [])) if isinstance(signins, dict) else 0
        manager_email = manager.get("mail") if isinstance(manager, dict) else None
        return UserContext(
            email=email,
            os_platform=choose_os_platform(hardware),
            department=user.get("department"),
            vip_status=is_vip(user.get("jobTitle")),
            assigned_hardware=hardware,
            recent_auth_failures=failures,
            manager_email=manager_email.lower() if manager_email else None,
            source="microsoft_graph",
        )


class OktaProvider:
    """Okta user profile and failed sign-ins (24h, System Log). Okta has no MDM data."""

    name = "okta"

    async def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        s = get_settings()
        resp = await _client().get(
            f"https://{s.okta_domain}{path}",
            params=params,
            headers={"Authorization": f"SSWS {s.okta_api_token.get_secret_value()}", "Accept": "application/json"},
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    async def lookup(self, tenant_id: str, email: str) -> UserContext | None:
        user = await self._get(f"/api/v1/users/{email}")
        if user is None:
            return None
        profile = user.get("profile", {})
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            logs = await self._get("/api/v1/logs", {
                "since": since,
                "filter": f'eventType eq "user.session.start" and outcome.result eq "FAILURE" '
                          f'and actor.alternateId eq "{email}"',
                "limit": "50",
            })
        except httpx.HTTPError:
            logs = []
        manager = (profile.get("managerEmail") or profile.get("manager") or "").lower()
        return UserContext(
            email=email,
            department=profile.get("department"),
            vip_status=is_vip(profile.get("title")),
            recent_auth_failures=len(logs or []),
            manager_email=manager if _EMAIL.match(manager) else None,
            source="okta",
        )


_cache_provider = DirectoryCacheProvider()
_live_providers: dict[IdentityProviderKind, IdentityProvider] = {}


def live_provider() -> IdentityProvider | None:
    kind = get_settings().identity_provider
    if kind is IdentityProviderKind.DIRECTORY_CACHE:
        return None
    if kind not in _live_providers:
        _live_providers[kind] = MicrosoftGraphProvider() if kind is IdentityProviderKind.MICROSOFT_GRAPH else OktaProvider()
    return _live_providers[kind]


async def _write_through(tenant_id: str, ctx: UserContext) -> None:
    try:
        await supabase_client.upsert(
            "tenant_directory",
            {
                "tenant_id": tenant_id,
                "email": ctx.email,
                "department": ctx.department,
                "manager_email": ctx.manager_email,
                "os_platform": ctx.os_platform.value,
                "vip_status": ctx.vip_status,
                "assigned_hardware": [h.model_dump(mode="json") for h in ctx.assigned_hardware],
                "recent_auth_failures": ctx.recent_auth_failures,
                "source": ctx.source,
                "synced_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="tenant_id,email",
        )
    except Exception:  # noqa: BLE001
        logger.warning("directory cache write-through failed tenant=%s", tenant_id)


def _unavailable(email: str) -> UserContext:
    return UserContext(email=email, source="unavailable")


@gated(Feature.CONTEXT_ENRICHMENT, default=None)
async def enrich(tenant_id: str, email: str, vision: VisionContext | None = None) -> UserContext | None:
    """UserContext for the reporter (Tier 1+). Never raises."""
    if not _EMAIL.match(email):
        return _unavailable(email)
    settings = get_settings()
    ctx: UserContext | None = None
    provider = live_provider()
    if provider is not None:
        try:
            ctx = await asyncio.wait_for(provider.lookup(tenant_id, email), settings.identity_lookup_timeout_seconds)
            if ctx is not None:
                await _write_through(tenant_id, ctx)
        except Exception as exc:  # noqa: BLE001
            logger.warning("identity lookup failed provider=%s err=%s; using cache", provider.name, type(exc).__name__)
            ctx = None
    if ctx is None:
        try:
            ctx = await _cache_provider.lookup(tenant_id, email)
        except Exception:  # noqa: BLE001
            logger.warning("directory cache lookup failed tenant=%s", tenant_id)
    if ctx is None:
        return _unavailable(email)

    # A screenshot of the user's actual screen beats the directory's guess.
    hint = next((e.os_platform_hint for e in (vision.extractions if vision else [])
                 if e.os_platform_hint is not OSPlatform.UNKNOWN), OSPlatform.UNKNOWN)
    chosen = choose_os_platform(ctx.assigned_hardware, hint) if hint is not OSPlatform.UNKNOWN else ctx.os_platform
    if chosen is not ctx.os_platform:
        ctx = ctx.model_copy(update={"os_platform": chosen})
    return ctx
