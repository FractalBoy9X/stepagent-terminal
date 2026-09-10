"""A single I/O worker; curses remains exclusively on the UI thread."""
from __future__ import annotations

import copy
import queue
import threading
import time
from pathlib import Path

from .core.adapters import IncrementalParser
from .core.graph_builder import build_execution_graph
from .source import JsonlTail, SessionCatalog


class Watcher:
    def __init__(self, root: Path, interval: float):
        self.catalog = SessionCatalog(root)
        self.interval = interval
        self.commands = queue.Queue()
        self.updates = queue.Queue(maxsize=2)
        self.catalog_updates = queue.Queue(maxsize=1)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name="session-reader")

    @staticmethod
    def publish(target, value):
        try:
            target.put_nowait(value)
        except queue.Full:
            try:
                target.get_nowait()
            except queue.Empty:
                pass
            target.put_nowait(value)

    def select(self, path: Path):
        self.commands.put(path)

    def run(self):
        tail = parser = None
        next_scan = 0.0
        version = 0
        snapshot = graph = None
        while not self.stop.is_set():
            try:
                if time.monotonic() >= next_scan:
                    self.publish(self.catalog_updates, (self.catalog.scan(), self.catalog.error))
                    next_scan = time.monotonic() + 3
                target = None
                while True:
                    try:
                        target = self.commands.get_nowait()
                    except queue.Empty:
                        break
                if target is not None:
                    tail = JsonlTail(target)
                    parser = IncrementalParser(target.name)
                    snapshot = graph = None
                    version = 0
                if tail is not None:
                    records, reset = tail.poll()
                    if reset:
                        parser = IncrementalParser(tail.path.name)
                    for record in records:
                        parser.feed(record)
                    if records or reset or snapshot is None:
                        doc = parser.snapshot()
                        # Raw source objects are append-only and never mutated by the UI.
                        snapshot = copy.copy(doc)
                        snapshot.raw_records = list(doc.raw_records)
                        snapshot.interactions = copy.deepcopy(doc.interactions)
                        graph = build_execution_graph(snapshot)
                        version += 1
                    self.publish(self.updates, dict(path=tail.path, doc=snapshot, graph=graph, version=version,
                        offset=tail.offset, size=tail.size, mtime=tail.mtime, bad_lines=tail.bad_lines,
                        error=tail.error, reset=reset, partial=bool(tail.buffer)))
                    if tail.offset < tail.size:
                        continue
            except Exception as exc:
                self.publish(self.updates, dict(error=f"Błąd odczytu: {type(exc).__name__}: {exc}", path=tail.path if tail else None))
            self.stop.wait(self.interval)
