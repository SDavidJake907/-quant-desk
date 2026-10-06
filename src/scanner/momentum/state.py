"""The tranche book on disk. The broker only knows total shares; this file
is the only record of which tranche owns what, so it is written atomically."""

from __future__ import annotations

import os
from pathlib import Path

from scanner.momentum.book import Book


def load(path: Path) -> Book | None:
    if not path.exists():
        return None
    return Book.model_validate_json(path.read_text(encoding="utf-8"))


def save(book: Book, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(book.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)
