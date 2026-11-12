"""Load a tenant's application catalog and user directory from CSV (Tier 1 data).

    python -m ingest.load_tenant_data --tenant acme-corp --catalog ingest/sample_catalog.csv
    python -m ingest.load_tenant_data --tenant acme-corp --directory ingest/sample_directory.csv
    add --dry-run to validate without writing

Catalog CSV columns:
    item_key, item_type (software|hardware), display_name, aliases (semicolon-separated),
    requires_manager_approval (true|false), approver_role (direct_manager|app_owner|it_manager),
    app_owner_email, provisioning_group, license_cost_usd, doc_id, active (true|false)

Directory CSV columns (only needed when IDENTITY_PROVIDER=directory_cache, or as a fallback):
    email, display_name, department, manager_email, os_platform, vip_status (true|false),
    devices (semicolon-separated "model|os_platform|os_version"), recent_auth_failures

Rows are validated with the same Pydantic contracts the agent uses, and upserted.
Re-running is safe.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import get_settings
from app.schemas.ticket import CatalogItem, HardwareAsset, OSPlatform, UserContext

_TRUE = {"true", "yes", "1", "y"}


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in _TRUE


def _blank_none(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def parse_catalog(path: Path, tenant: str) -> list[dict[str, Any]]:
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for lineno, raw in enumerate(csv.DictReader(f), start=2):
            try:
                item = CatalogItem(
                    tenant_id=tenant,
                    item_key=raw["item_key"].strip().lower(),
                    item_type=raw.get("item_type", "software").strip().lower(),
                    display_name=raw["display_name"].strip(),
                    aliases=[a.strip() for a in (raw.get("aliases") or "").split(";") if a.strip()],
                    requires_manager_approval=_bool(raw.get("requires_manager_approval"), True),
                    approver_role=(raw.get("approver_role") or "direct_manager").strip(),
                    app_owner_email=(_blank_none(raw.get("app_owner_email")) or "").lower() or None,
                    provisioning_group=_blank_none(raw.get("provisioning_group")),
                    license_cost_usd=float(raw["license_cost_usd"]) if _blank_none(raw.get("license_cost_usd")) else None,
                    doc_id=_blank_none(raw.get("doc_id")),
                    active=_bool(raw.get("active"), True),
                )
            except (KeyError, ValueError, ValidationError) as exc:
                raise SystemExit(f"{path}:{lineno}: invalid catalog row: {exc}") from None
            rows.append(item.model_dump(mode="json"))
    _no_duplicates(rows, "item_key", path)
    return rows


def parse_directory(path: Path, tenant: str) -> list[dict[str, Any]]:
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for lineno, raw in enumerate(csv.DictReader(f), start=2):
            try:
                devices = []
                for spec in (raw.get("devices") or "").split(";"):
                    if not spec.strip():
                        continue
                    model, _, rest = spec.partition("|")
                    platform, _, version = rest.partition("|")
                    devices.append(HardwareAsset(model=_blank_none(model), os_platform=OSPlatform(
                        (platform or "unknown").strip().lower()), os_version=_blank_none(version)))
                ctx = UserContext(
                    email=raw["email"].strip().lower(),
                    os_platform=OSPlatform((raw.get("os_platform") or "unknown").strip().lower()),
                    department=_blank_none(raw.get("department")),
                    vip_status=_bool(raw.get("vip_status"), False),
                    assigned_hardware=devices,
                    recent_auth_failures=int(raw.get("recent_auth_failures") or 0),
                    manager_email=(_blank_none(raw.get("manager_email")) or "").lower() or None,
                    source="directory_cache",
                )
            except (KeyError, ValueError, ValidationError) as exc:
                raise SystemExit(f"{path}:{lineno}: invalid directory row: {exc}") from None
            rows.append({
                "tenant_id": tenant, "email": ctx.email, "display_name": _blank_none(raw.get("display_name")),
                "department": ctx.department, "manager_email": ctx.manager_email, "os_platform": ctx.os_platform.value,
                "vip_status": ctx.vip_status,
                "assigned_hardware": [h.model_dump(mode="json") for h in ctx.assigned_hardware],
                "recent_auth_failures": ctx.recent_auth_failures, "source": "manual",
            })
    _no_duplicates(rows, "email", path)
    return rows


def _no_duplicates(rows: list[dict], key: str, path: Path) -> None:
    seen: set[str] = set()
    for row in rows:
        if row[key] in seen:
            raise SystemExit(f"{path}: duplicate {key} {row[key]!r}")
        seen.add(row[key])


def upsert(table: str, rows: list[dict], on_conflict: str) -> None:
    settings = get_settings()
    key = settings.supabase_service_key.get_secret_value()
    resp = httpx.post(
        f"{settings.supabase_url.rstrip('/')}/rest/v1/{table}",
        params={"on_conflict": on_conflict},
        json=rows,
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"},
        timeout=30.0,
    )
    if resp.status_code >= 300:
        raise SystemExit(f"upsert into {table} failed ({resp.status_code}): {resp.text[:500]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not (args.catalog or args.directory):
        parser.error("pass --catalog and/or --directory")

    if args.catalog:
        rows = parse_catalog(args.catalog, args.tenant)
        print(f"catalog: {len(rows)} valid items")
        if not args.dry_run:
            upsert("app_catalog", rows, "tenant_id,item_key")
            print("catalog: upserted")
    if args.directory:
        rows = parse_directory(args.directory, args.tenant)
        print(f"directory: {len(rows)} valid users")
        if not args.dry_run:
            upsert("tenant_directory", rows, "tenant_id,email")
            print("directory: upserted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
