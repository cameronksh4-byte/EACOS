"""Restricted execution for untrusted rule code.

Use case: a business owner (or an agent) writes a custom audit rule such as
``result = total > 5000 and vendor_name not in approved_vendors``. That code must not
be able to read files, open network connections, import modules or run forever.

Two layers:
  1. **Static allowlist** (``check_code``): parse the code and accept only a small set
     of syntax - arithmetic, comparisons, boolean logic, if/for, assignments and calls
     to a few safe builtins. No imports, no attribute access, no dunder names.
  2. **Isolated subprocess** (``run_restricted``): a separate Python process in isolated
     mode with an empty environment, a throwaway working directory, CPU/memory/file
     limits and a wall-clock timeout. Inputs go in and the result comes out as JSON.

Honest limits: this is defense in depth, not a container. For running genuinely
hostile code in production, put the runner inside a locked-down container
(e.g. ``docker run --network none --read-only --memory 256m --pids-limit 64 ...``)
or a microVM. The static check is what makes this layer safe enough for rule snippets.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any

SAFE_BUILTINS = ("abs", "min", "max", "round", "sum", "len", "all", "any", "sorted", "range", "int", "float",
                 "str", "bool", "list", "dict", "set", "tuple")

_ALLOWED_NODES = (
    ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.If, ast.For, ast.Pass, ast.Break, ast.Continue,
    ast.Name, ast.Load, ast.Store, ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict,
    ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Call, ast.Subscript, ast.Slice,
    ast.comprehension, ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp, ast.keyword,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow, ast.USub, ast.UAdd, ast.Not,
    ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot,
)


class UnsafeCode(ValueError):
    pass


@dataclass
class SandboxResult:
    ok: bool
    result: Any = None
    error: str | None = None


def check_code(code: str) -> None:
    """Raise UnsafeCode unless the code uses only allowlisted syntax and names."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise UnsafeCode(f"syntax error: {exc.msg} (line {exc.lineno})") from None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise UnsafeCode(f"'{type(node).__name__}' is not allowed (line {getattr(node, 'lineno', '?')})")
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            raise UnsafeCode(f"name {node.id!r} is not allowed")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in SAFE_BUILTINS:
                name = (f".{node.func.attr}()" if isinstance(node.func, ast.Attribute)
                        else getattr(node.func, "id", type(node.func).__name__))
                raise UnsafeCode(f"call to {name!r} is not allowed; allowed: {', '.join(SAFE_BUILTINS)}")


# Runs inside the child process. Reads {"code", "inputs"} from stdin, prints {"result"} or {"error"}.
_RUNNER = r"""
import json, sys
try:
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (16, 16))
    try:
        mb = int(sys.argv[1]) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mb, mb))
    except (ValueError, OSError):
        pass  # not supported on every OS (e.g. macOS)
except ImportError:
    pass  # Windows: rely on the timeout and static check
payload = json.loads(sys.stdin.read())
import builtins
safe = {name: getattr(builtins, name) for name in payload["builtins"]}
scope = dict(payload["inputs"])
try:
    exec(compile(payload["code"], "<rule>", "exec"), {"__builtins__": safe}, scope)
    print(json.dumps({"result": scope.get("result")}, default=str))
except Exception as exc:
    print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
"""


def run_restricted(code: str, inputs: dict[str, Any] | None = None, *, timeout: float = 3.0,
                   memory_mb: int = 256) -> SandboxResult:
    """Validate, then run code in an isolated subprocess. The code sets ``result``."""
    try:
        check_code(code)
    except UnsafeCode as exc:
        return SandboxResult(ok=False, error=f"rejected: {exc}")
    return _execute(code, inputs, timeout=timeout, memory_mb=memory_mb)


def _execute(code: str, inputs: dict[str, Any] | None, *, timeout: float, memory_mb: int) -> SandboxResult:
    """The subprocess layer on its own (no static check). Tests use it to prove the second layer holds."""
    payload = json.dumps({"code": code, "inputs": inputs or {}, "builtins": SAFE_BUILTINS}, default=str)
    with tempfile.TemporaryDirectory() as workdir:
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-S", "-c", _RUNNER, str(memory_mb)],
                input=payload, capture_output=True, text=True, timeout=timeout, cwd=workdir, env={},
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(ok=False, error=f"timed out after {timeout}s")
    if proc.returncode < 0:
        return SandboxResult(ok=False, error=f"killed by the OS (signal {-proc.returncode}): resource limit exceeded")
    if proc.returncode != 0 or not proc.stdout.strip():
        return SandboxResult(ok=False, error=f"runner exited with code {proc.returncode}: {proc.stderr.strip()[-200:]}")
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    if "error" in out:
        return SandboxResult(ok=False, error=out["error"])
    return SandboxResult(ok=True, result=out["result"])
