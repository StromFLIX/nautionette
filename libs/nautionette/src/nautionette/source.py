"""Reading a workflow file without running it."""

from __future__ import annotations

import ast
import difflib
import re
import tomllib
from typing import Any

WORKFLOW_DECORATORS = ("workflow.defn", "defn")

# PEP 723 inline metadata, the same block `uv run` reads from a script.
_SCRIPT_BLOCK = re.compile(
    r"(?m)^# /// script\s*$\s(?P<body>(?:^#(?:| .*)$\s)+)^# ///\s*$",
)
# A dependency is a name, not an option: never let a spec become a uv flag.
_DEPENDENCY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(\[[A-Za-z0-9,._-]+\])?\s*[<>=!~^ 0-9a-zA-Z.,*+-]*$")

MAX_DEPENDENCIES = 20


class SourceError(ValueError):
    """Raised when a workflow file cannot be understood."""


def parse_dependencies(source: str) -> list[str]:
    """Third-party packages a workflow declares in its PEP 723 header."""
    match = _SCRIPT_BLOCK.search(source)
    if not match:
        return []
    body = "".join(
        line[2:] if line.startswith("# ") else line[1:]
        for line in match.group("body").splitlines(keepends=True)
    )
    try:
        meta = tomllib.loads(body)
    except tomllib.TOMLDecodeError as exc:
        raise SourceError(f"the script block is not valid TOML: {exc}") from exc

    declared = meta.get("dependencies", [])
    if not isinstance(declared, list) or not all(isinstance(item, str) for item in declared):
        raise SourceError("dependencies must be a list of strings")
    if len(declared) > MAX_DEPENDENCIES:
        raise SourceError(f"more than {MAX_DEPENDENCIES} dependencies declared")

    out: list[str] = []
    for spec in declared:
        cleaned = spec.strip()
        if not _DEPENDENCY.match(cleaned):
            raise SourceError(f"dependency {spec!r} is not a plain package requirement")
        out.append(cleaned)
    return out


def parse_manifest(source: str) -> dict[str, Any]:
    """Pull `MANIFEST = {...}` out of a module without importing it."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SourceError(f"line {exc.lineno}: {exc.msg}") from exc

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "MANIFEST":
                try:
                    value = ast.literal_eval(node.value)
                except ValueError as exc:
                    raise SourceError(f"MANIFEST must be a literal dict: {exc}") from exc
                if not isinstance(value, dict):
                    raise SourceError("MANIFEST must be a dict")
                return value
    raise SourceError("no module level MANIFEST dict found")


def find_workflow_classes(source: str) -> list[str]:
    """Names of classes decorated with @workflow.defn."""
    tree = ast.parse(source)
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for decorator in node.decorator_list:
            call = decorator.func if isinstance(decorator, ast.Call) else decorator
            dotted = _dotted_name(call)
            if dotted in WORKFLOW_DECORATORS:
                found.append(node.name)
                break
    return found


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr}"
    return ""


def unified_diff(old: str, new: str, name: str) -> str:
    """A diff a human can read before approving a deploy."""
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            n=3,
        )
    )
