"""Incremental, lossless terminal rendering. Work is bounded by viewport demand."""
from __future__ import annotations

import json
import re
import time
import unicodedata
from itertools import chain

from .details import Span, cells, safe, syntax


def json_spans(value, level=0):
    """Pretty JSON without serializing a whole large object/string up front."""
    if isinstance(value, dict):
        yield Span("{", 0)
        for n, (key, child) in enumerate(value.items()):
            yield Span(("," if n else "") + "\n" + "  " * (level + 1))
            yield from string_spans(str(key), 2)
            yield Span(": ")
            yield from json_spans(child, level + 1)
        yield Span(("\n" + "  " * level if value else "") + "}")
    elif isinstance(value, (list, tuple)):
        yield Span("[")
        for n, child in enumerate(value):
            yield Span(("," if n else "") + "\n" + "  " * (level + 1))
            yield from json_spans(child, level + 1)
        yield Span(("\n" + "  " * level if value else "") + "]")
    elif isinstance(value, str):
        yield from string_spans(value, 3)
    else:
        yield Span(json.dumps(value, ensure_ascii=False), 5)


def string_spans(value, color):
    yield Span('"', color)
    for start in range(0, len(value), 1024):
        # Escaping also preserves control characters safely in fields / RAW.
        chunk = json.dumps(value[start:start + 1024], ensure_ascii=False)[1:-1]
        chunk = "".join(json.dumps(char, ensure_ascii=True)[1:-1]
                        if unicodedata.category(char).startswith("C") else char for char in chunk)
        yield Span(chunk, color)
    yield Span('"', color)


def content_spans(value, mode="markdown", color=0):
    if not isinstance(value, str):
        yield from json_spans(value)
        return
    value = str(value)
    line_start, tint, bold = True, color, False
    for match in re.finditer(r"[^\n]{1,1024}|\n", value):
        chunk = match.group()
        if line_start:
            bold = chunk.startswith(("@@", "***")) if mode == "diff" else chunk.startswith("#")
            tint = (2 if chunk.startswith(("@@", "***", "diff ", "---", "+++")) else
                    3 if chunk.startswith("+") else 7 if chunk.startswith("-") else color) if mode == "diff" else color
        if mode in {"code", "command", "json"}:
            yield from syntax(chunk, "json" if mode == "json" else "code", color)
        elif mode == "markdown" and not bold:
            for part in re.split(r"(`[^`]+`|\*\*[^*]+\*\*)", chunk):
                yield Span(part, 3 if part.startswith("`") else color, part.startswith("**"))
        else:
            yield Span(chunk, 2 if bold and mode == "markdown" else tint, bold)
        line_start = chunk == "\n"


def wrapped_rows(rows, width):
    """Rows may be generators; only the short leading prefix is buffered."""
    width = max(2, width)
    for row in rows:
        spans = iter(row)
        prefix, rest = [], ()
        for span in spans:
            if span.hang is None:
                rest = (span,)
                break
            prefix.append(span)
        repeat = [Span(span.hang, span.color, span.bold) for span in prefix if span.hang]
        lead = sum(cells(safe(span.text)) for span in prefix)
        pad = sum(cells(safe(span.text)) for span in repeat)
        if prefix and width - max(lead, pad) < 8:  # Too narrow to keep a hanging marker.
            rest, prefix, repeat, lead, pad = chain(prefix, rest), [], [], 0, 0
        current, used = list(prefix), lead
        for span in chain(rest, spans):
            fragment = []
            text = span.fill * max(0, width - used) if span.fill and not span.text else span.text
            for char in safe(text):
                size = cells(char)
                if char == "\n" or used + size > width:
                    if fragment:
                        current.append(Span("".join(fragment), span.color, span.bold))
                        fragment = []
                    yield current
                    current, used = list(repeat), pad
                    if char == "\n":
                        continue
                fragment.append(char)
                used += size
            if fragment:
                current.append(Span("".join(fragment), span.color, span.bold))
        yield current


class PagedRows:
    """Only visited pages are materialized; no character/row cutoff."""
    def __init__(self, rows, width):
        self.iterator = iter(wrapped_rows(rows, width))
        self.rows = []
        self.complete = False

    def ensure(self, end, budget_ms=8):
        deadline = time.monotonic() + budget_ms / 1000
        while not self.complete and len(self.rows) < end:
            try:
                self.rows.append(next(self.iterator))
            except StopIteration:
                self.complete = True
            if time.monotonic() >= deadline:
                break
        return self.rows
