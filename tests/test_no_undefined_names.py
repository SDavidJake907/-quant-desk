"""Every name a module uses must be bound somewhere in it.

THE BUG THIS CATCHES, WHICH HAPPENED THREE TIMES IN ONE DAY
A patch adds a call to `build_id()` in five places and the matching import
silently fails to apply. Nothing complains: the module imports fine, the test
suite passes, and every dry run works — because the call sites sit inside a
branch that only executes when Ollama is unreachable.

The crash then waits for the exact degraded condition the code was written to
handle, and fires on a scheduled unattended run.

Unit tests do not cover script bodies, and `import` alone cannot catch a name
used inside a function. This does: it walks the AST, collects every name bound
anywhere in the module, and reports every load of something that was never
bound.

DELIBERATELY OVER-PERMISSIVE ABOUT SCOPE
All bindings are pooled rather than tracked per scope, so a genuine
use-before-assignment across scopes slips through. That is fine. The target is
the name that is bound NOWHERE, which is what a failed import patch leaves
behind, and pooling keeps false positives near zero.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_FILES = sorted(
    [p for p in (_ROOT / "src").rglob("*.py")]
    + [p for p in (_ROOT / "scripts").rglob("*.py")]
)

_BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__"}


class _Bindings(ast.NodeVisitor):
    """Every name this module binds, by any means."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.star_import = False

    def visit_Import(self, node):
        for alias in node.names:
            self.names.add(alias.asname or alias.name.split(".")[0])
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        for alias in node.names:
            if alias.name == "*":
                self.star_import = True     # cannot know what it brought in
            else:
                self.names.add(alias.asname or alias.name)
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)
        self.generic_visit(node)

    def _function(self, node):
        self.names.add(node.name)
        args = node.args
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            self.names.add(arg.arg)
        if args.vararg:
            self.names.add(args.vararg.arg)
        if args.kwarg:
            self.names.add(args.kwarg.arg)
        self.generic_visit(node)

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_Lambda(self, node):
        """A lambda binds its parameters too. Missing this reported every
        `key=lambda t: t.x` in the repo as an undefined name — the checker's
        own bug, and a reminder that a tool finding faults must be checked
        against known-good code before its findings are believed."""
        args = node.args
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
            self.names.add(arg.arg)
        if args.vararg:
            self.names.add(args.vararg.arg)
        if args.kwarg:
            self.names.add(args.kwarg.arg)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self.names.add(node.name)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node):
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_Global(self, node):
        self.names.update(node.names)
        self.generic_visit(node)

    visit_Nonlocal = visit_Global


def _loaded(tree: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


@pytest.mark.parametrize("path", _FILES, ids=lambda p: str(p.relative_to(_ROOT)))
def test_every_name_is_bound_somewhere(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bindings = _Bindings()
    bindings.visit(tree)
    if bindings.star_import:
        pytest.skip("star import: bindings cannot be determined statically")
    undefined = sorted(_loaded(tree) - bindings.names - _BUILTINS)
    assert not undefined, (
        f"{path.relative_to(_ROOT)} uses names bound nowhere in the module: "
        f"{undefined}. A failed import patch leaves exactly this."
    )
