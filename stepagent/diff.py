"""Patch parsing and diff rendering. Line numbers are counted, never guessed."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .details import Span


FILE_MARKER = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.*)$")
MOVE_MARKER = re.compile(r"^\*\*\* Move to: (.*)$")
GIT_MARKER = re.compile(r"^diff --git (?:a/)?(\S+) (?:b/)?(\S+)\s*$")
OLD_MARKER = re.compile(r"^--- (?:a/)?(.*?)(?:\t.*)?$")
NEW_MARKER = re.compile(r"^\+\+\+ (?:b/)?(.*?)(?:\t.*)?$")
NUMBERED_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
BARE_HUNK = re.compile(r"^@@(.*)$")
ENVELOPE = re.compile(r"^\*\*\* (?:Begin Patch|End Patch|Add File:|Update File:|Delete File:|Move to:)", re.M)
OPERATIONS = {"Add": "add", "Update": "update", "Delete": "delete"}
# Terminal palette from ui.py: 1 white, 2 cyan, 3 green, 4 yellow, 5 magenta,
# 6 blue, 7 red. Additions stay green, removals red, the gutter stays blue.
LABELS = {"add": ("NOWY PLIK", 3), "update": ("ZMIANA", 4), "delete": ("USUNIĘTY PLIK", 7), "unknown": ("PLIK", 4)}
TINTS = {"+": 3, "-": 7, " ": 0, "\\": 6, "?": 4}


@dataclass(frozen=True)
class Line:
    kind: str  # "+", "-", " " (context), "\\" (no-newline note), "?" (unrecognized)
    text: str
    old: int | None = None
    new: int | None = None


@dataclass(frozen=True)
class Hunk:
    """`absolute` marks numbers taken from the source, not counted inside a hunk."""
    lines: tuple[Line, ...]
    anchor: str = ""
    absolute: bool = False


@dataclass(frozen=True)
class FilePatch:
    operation: str
    path: str
    hunks: tuple[Hunk, ...]
    move_to: str = ""
    notes: tuple[str, ...] = ()

    @property
    def added(self):
        return sum(1 for hunk in self.hunks for line in hunk.lines if line.kind == "+")

    @property
    def removed(self):
        return sum(1 for hunk in self.hunks for line in hunk.lines if line.kind == "-")


@dataclass(frozen=True)
class Patch:
    files: tuple[FilePatch, ...]
    notes: tuple[str, ...] = ()

    def __bool__(self):
        return bool(self.files)


class _Builder:
    """Accumulates one patch; hunks close on a marker or an exhausted count."""
    def __init__(self):
        self.files, self.notes = [], []
        self.file = None
        self.lines = []
        self.anchor = ""
        self.absolute = False
        self.old = self.new = None
        self.old_left = self.new_left = None
        self.seq_old = self.seq_new = 1

    def open_file(self, operation, path):
        self.close_file()
        self.seq_old = self.seq_new = 1
        self.file = dict(operation=operation, path=path, move_to="", hunks=[], notes=[])

    def open_counted_hunk(self, anchor):
        """A bare `@@` carries no position, so counting continues in the file."""
        self.close_hunk()
        self.open_hunk(self.seq_old, self.seq_new, False, anchor)

    def open_hunk(self, old, new, absolute, anchor="", old_left=None, new_left=None):
        self.close_hunk()
        if self.file is None:
            self.open_file("unknown", "")
        self.old, self.new, self.absolute = old, new, absolute
        self.anchor = anchor.strip()
        self.old_left, self.new_left = old_left, new_left

    def open_body(self):
        """A body line without a hunk header: Add/Delete start at line 1."""
        if self.lines or self.old is not None or self.new is not None:
            return
        operation = self.file["operation"] if self.file else "unknown"
        if operation == "add":
            self.open_hunk(None, 1, True)
        elif operation == "delete":
            self.open_hunk(1, None, True)
        else:
            self.open_hunk(1, 1, False)

    def add(self, kind, text):
        old = new = None
        if kind == "-" and self.old is not None:
            old, self.old = self.old, self.old + 1
        elif kind == "+" and self.new is not None:
            new, self.new = self.new, self.new + 1
        elif kind == " ":
            if self.old is not None:
                old, self.old = self.old, self.old + 1
            if self.new is not None:
                new, self.new = self.new, self.new + 1
        self.lines.append(Line(kind, text, old, new))
        if kind in {"-", " "} and self.old_left:
            self.old_left -= 1
        if kind in {"+", " "} and self.new_left:
            self.new_left -= 1

    def counting(self):
        return self.old_left is not None and (self.old_left > 0 or self.new_left > 0)

    def in_hunk(self):
        return bool(self.lines) or self.old is not None or self.new is not None

    def note(self, text):
        (self.file["notes"] if self.file else self.notes).append(text)

    def close_hunk(self):
        self.seq_old = self.old if self.old is not None else self.seq_old
        self.seq_new = self.new if self.new is not None else self.seq_new
        if self.lines:
            self.file["hunks"].append(Hunk(tuple(self.lines), self.anchor, self.absolute))
        self.lines = []
        self.anchor = ""
        self.old = self.new = self.old_left = self.new_left = None

    def close_file(self):
        self.close_hunk()
        if self.file is not None:
            self.files.append(FilePatch(self.file["operation"], self.file["path"],
                                        tuple(self.file["hunks"]), self.file["move_to"], tuple(self.file["notes"])))
        self.file = None

    def result(self):
        self.close_file()
        return Patch(tuple(self.files), tuple(self.notes))


def parse_patch(text):
    """Read the apply_patch envelope and unified diffs without guessing.

    Inside a hunk the first character decides the line kind, so a removed
    `--- something` stays a removal instead of being read as a file header.
    """
    if not isinstance(text, str) or not text.strip():
        return Patch(())
    builder = _Builder()
    # Decided once: inside the apply_patch envelope `+++ x` is an added line,
    # never a unified-diff header, so the two formats never cross over.
    envelope = bool(ENVELOPE.search(text))
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # A trailing newline is not an empty context line.
    for raw in lines:
        if raw.startswith("*** "):
            file_marker = FILE_MARKER.match(raw)
            move_marker = MOVE_MARKER.match(raw)
            if file_marker:
                builder.open_file(OPERATIONS[file_marker.group(1)], file_marker.group(2).strip())
            elif move_marker and builder.file is not None:
                builder.close_hunk()
                builder.file["move_to"] = move_marker.group(1).strip()
            elif raw not in {"*** Begin Patch", "*** End Patch"}:
                builder.note(raw)
            else:
                builder.close_file()
            continue
        if raw.startswith("@@"):
            numbered = NUMBERED_HUNK.match(raw)
            if numbered:
                old, old_count, new, new_count, anchor = numbered.groups()
                builder.open_hunk(int(old), int(new), True, anchor,
                                  int(old_count or 1), int(new_count or 1))
            else:
                builder.open_counted_hunk(BARE_HUNK.match(raw).group(1))
            continue
        if not envelope and not builder.counting() and not builder.in_hunk():
            git = GIT_MARKER.match(raw)
            if git:
                builder.open_file("update", git.group(2))
                continue
            old_marker, new_marker = OLD_MARKER.match(raw), NEW_MARKER.match(raw)
            if old_marker and builder.file is None:
                builder.open_file("update", old_marker.group(1).strip())
                continue
            if old_marker or new_marker:
                path = (new_marker or old_marker).group(1).strip()
                if new_marker and builder.file is not None and path not in {"", "/dev/null"}:
                    builder.file["path"] = path
                continue
        if raw[:1] in {"+", "-", " "}:
            builder.open_body()
            builder.add(raw[0], raw[1:])
        elif raw.startswith("\\") and builder.in_hunk():
            builder.add("\\", raw)
        elif raw == "" and builder.in_hunk():
            builder.add(" ", "")
        elif raw == "" or raw.startswith("\\"):
            builder.note(raw) if raw else None
        elif builder.in_hunk():
            builder.add("?", raw)
        else:
            builder.note(raw)
    return builder.result()


def _width(patch_file):
    numbers = [n for hunk in patch_file.hunks for line in hunk.lines
               for n in (line.old, line.new) if n is not None]
    return min(6, max(2, len(str(max(numbers))) if numbers else 2))


def _gutter(line, width):
    old = f"{line.old:>{width}}" if line.old is not None else " " * width
    new = f"{line.new:>{width}}" if line.new is not None else " " * width
    return f"{old} {new} "


def _hunks(count):
    tail = "hunk" if count == 1 else "hunki" if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14} else "hunków"
    return f"{count} {tail}"


def numbering(patch_file):
    """One label per file while every hunk shares the same basis."""
    bases = {hunk.absolute for hunk in patch_file.hunks}
    if len(bases) != 1:
        return ""
    return "numery linii ze źródła" if bases == {True} else "numeracja kolejna w patchu"


def file_summary(patch_file):
    label, color = LABELS.get(patch_file.operation, LABELS["unknown"])
    row = [Span(label, color, True), Span(" · ", 6), Span(patch_file.path or "(ścieżka niepodana)", 1, True)]
    if patch_file.move_to:
        row += [Span(" → ", 6), Span(patch_file.move_to, 1, True)]
    if patch_file.hunks:
        row += [Span(" · ", 6), Span(f"+{patch_file.added}", 3), Span(" ", 0), Span(f"−{patch_file.removed}", 7),
                Span(f" · {_hunks(len(patch_file.hunks))}", 6)]
    else:
        row += [Span(" · bez zapisanych linii", 6)]
    return row


def diff_rows(text):
    """Styled rows for a patch; markers repeat on wrapped lines via `hang`."""
    patch = parse_patch(text)
    if not patch:
        yield [Span("Nie rozpoznano formatu patcha — treść bez zmian poniżej.", 4)]
        for line in str(text).split("\n"):
            yield [Span(line, 0)]
        return
    for note in patch.notes:
        yield [Span(note, 6)]
    for number, patch_file in enumerate(patch.files):
        if number:
            yield []
        yield file_summary(patch_file)
        width = _width(patch_file)
        shared = numbering(patch_file)
        if shared:
            yield [Span(shared, 6)]
        for index, hunk in enumerate(patch_file.hunks, 1):
            basis = "" if shared else (" · numery linii ze źródła" if hunk.absolute else " · numeracja kolejna w patchu")
            anchor = f" · {hunk.anchor}" if hunk.anchor else ""
            yield [Span(f"── hunk {index}/{len(patch_file.hunks)}{basis}{anchor} ──", 6)]
            for line in hunk.lines:
                tint = TINTS.get(line.kind, 0)
                marker = line.kind if line.kind in {"+", "-"} else " "
                gutter = _gutter(line, width)
                yield [Span(gutter, 6, hang=" " * len(gutter)),
                       Span("│", 6, hang="│"),
                       Span(marker, tint, marker != " ", hang=marker),
                       Span(" ", 0, hang=" "),
                       Span(line.text, tint)]
        for note in patch_file.notes:
            yield [Span(note, 6)]
