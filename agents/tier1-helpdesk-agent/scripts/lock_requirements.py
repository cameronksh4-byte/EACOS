"""Resolve requirements.in into requirements.txt for the production target.

The image runs Python 3.12 on linux/arm64 (Graviton), which may differ from the
machine you run this on. pip's resolver is therefore asked for binary wheels
for that exact platform, so the lock matches what the Docker build installs
and never needs a compiler.

    python scripts/lock_requirements.py           # re-resolve and write requirements.txt
    python scripts/lock_requirements.py --check   # CI: lock pins every direct dep within its range

--check deliberately doesn't re-resolve: new upstream releases (boto3 ships
almost daily) would make that fail for reasons unrelated to the code.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ["manylinux_2_28_aarch64", "manylinux_2_17_aarch64", "manylinux2014_aarch64"]
HEADER = """\
# Locked runtime dependencies for the production image (Python 3.12, linux/arm64).
# Generated from requirements.in by `make lock` (scripts/lock_requirements.py). Do not edit by hand.
"""


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def direct_requirements() -> dict[str, str]:
    """Normalised name -> extras suffix (e.g. '[bedrock]') for each direct dependency."""
    out: dict[str, str] = {}
    for line in (ROOT / "requirements.in").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"([A-Za-z0-9_.-]+)(\[[^\]]+\])?", line)
        if m:
            out[_norm(m.group(1))] = m.group(2) or ""
    return out


def check() -> int:
    from packaging.requirements import Requirement

    pins: dict[str, str] = {}
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            req = Requirement(line)
            pins[_norm(req.name)] = next(iter(req.specifier)).version
    problems = []
    for line in (ROOT / "requirements.in").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        req = Requirement(line)
        name = _norm(req.name)
        if name not in pins:
            problems.append(f"{name}: not in requirements.txt")
        elif not req.specifier.contains(pins[name], prereleases=True):
            problems.append(f"{name}=={pins[name]} does not satisfy '{req.specifier}'")
    for problem in problems:
        print(f"lock out of date: {problem}")
    if not problems:
        print(f"lock ok: {len(pins)} pins satisfy requirements.in")
    return 1 if problems else 0


def main() -> int:
    if "--check" in sys.argv[1:]:
        return check()
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--dry-run", "--ignore-installed",
               "--only-binary=:all:", "--python-version", "3.12", "--implementation", "cp", "--abi", "cp312",
               "--target", str(Path(tmp) / "t"), "--report", str(report), "-r", str(ROOT / "requirements.in")]
        for platform in PLATFORMS:
            cmd += ["--platform", platform]
        subprocess.run(cmd, check=True)
        installs = json.loads(report.read_text())["install"]

    direct = direct_requirements()
    lines = [HEADER]
    for item in sorted(installs, key=lambda i: _norm(i["metadata"]["name"])):
        name, version = _norm(item["metadata"]["name"]), item["metadata"]["version"]
        if name in direct:
            lines.append(f"{name}{direct[name]}=={version}")
        else:
            lines.append(f"{name}=={version}  # transitive")
    (ROOT / "requirements.txt").write_text("\n".join(lines) + "\n")
    print(f"locked {len(installs)} packages -> requirements.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
