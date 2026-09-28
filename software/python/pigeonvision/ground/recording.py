"""Bounded, byte-preserving receive-side MPEG-TS recording.

The file is a prefix of received UDP payloads, including metadata and damaged
transport packets. A recording failure permanently disables this tap; it never
blocks live demux waiting for disk or queue space. Normal close drains and fsyncs.
"""
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import threading
import time

LOG = logging.getLogger(__name__)


class TransportRecorder:
    def __init__(self, path: str | Path, *, queue_size: int = 512, on_error=None):
        if queue_size < 1:
            raise ValueError("Transport recording queue size must be positive")
        self.path = Path(path)
        self.evidence_path = self.path.with_name(self.path.name + ".recording.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() or self.evidence_path.exists():
            raise FileExistsError(f"Transport recording or evidence already exists: {self.path}")
        self._file = self.path.open("xb", buffering=0)
        try:
            self._evidence = self.evidence_path.open("x", encoding="utf-8")
        except BaseException:
            self._file.close()
            self.path.unlink()  # Only our newly created, still-empty file.
            raise
        self._queue = queue.Queue(maxsize=queue_size)
        self._lock = threading.Lock()
        self._on_error = on_error
        self._accepting = True
        self._closing = False
        self._state = "recording"
        self._error = None
        self._received_bytes = self._accepted_bytes = self._written_bytes = 0
        self._received_datagrams = self._dropped_datagrams = 0
        self._digest = hashlib.sha256()
        self._started = time.time()
        self._finished = None
        self._sha256 = None
        self._worker = threading.Thread(target=self._run, name="pigeonvision-transport-recording", daemon=True)
        try:
            self._persist()
            self._worker.start()
        except BaseException:
            self._file.close()
            self._evidence.close()
            raise

    def status(self):
        with self._lock:
            return {"path": str(self.path), "evidence_path": str(self.evidence_path),
                    "state": self._state, "error": self._error,
                    "received_bytes": self._received_bytes, "accepted_bytes": self._accepted_bytes,
                    "written_bytes": self._written_bytes, "received_datagrams": self._received_datagrams,
                    "dropped_datagrams": self._dropped_datagrams, "queue_depth": self._queue.qsize(),
                    "queue_capacity_datagrams": self._queue.maxsize,
                    "started_unix": self._started, "finished_unix": self._finished, "sha256": self._sha256}

    def _fail(self, message):
        with self._lock:
            first = self._error is None
            if first:
                self._error = str(message)
            self._accepting = False
            self._state = "failed"
        if first:
            self._notify_failure(message)

    def _notify_failure(self, message):
        LOG.error("Transport recording disabled: %s", message)
        if self._on_error:
            try:
                self._on_error(str(message))
            except Exception:
                LOG.exception("Could not publish transport recording error")

    def submit(self, datagram: bytes) -> bool:
        """Called once per UDP recv, before any demux read slicing or auditing."""
        if not isinstance(datagram, bytes) or len(datagram) > 65535:
            raise ValueError("Expected one UDP datagram of at most 65535 bytes")
        full = False
        with self._lock:
            self._received_datagrams += 1
            self._received_bytes += len(datagram)
            if not self._accepting:
                self._dropped_datagrams += 1
                return False
            try:
                self._queue.put_nowait(datagram)
                self._accepted_bytes += len(datagram)
            except queue.Full:
                self._accepting = False
                self._dropped_datagrams += 1
                self._state = "failed"
                self._error = "Recording queue full; saved transport ends before the dropped datagram"
                full = True
        if full:
            self._notify_failure(self._error)
            return False
        return True

    def _write(self, data: bytes):
        view = memoryview(data)
        while view:
            count = self._file.write(view)
            if count is None or count <= 0:
                raise OSError("Transport recording write made no progress")
            self._digest.update(view[:count])
            with self._lock:
                self._written_bytes += count
            view = view[count:]

    def _persist(self):
        evidence = {"schema_version": 1, "type": "received_transport_recording", **self.status(),
                    "complete": self._state == "complete",
                    "scope": "UDP payloads received by this ground process; upstream loss is preserved"}
        self._evidence.seek(0)
        json.dump(evidence, self._evidence, indent=2)
        self._evidence.write("\n")
        self._evidence.truncate()
        self._evidence.flush()
        os.fsync(self._evidence.fileno())

    def _run(self):
        try:
            while True:
                try:
                    datagram = self._queue.get(timeout=.1)
                except queue.Empty:
                    with self._lock:
                        if self._closing or not self._accepting:
                            break
                    continue
                self._write(datagram)
        except Exception as exc:
            self._fail(f"Transport file write failed: {exc}")
            while True:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
        finally:
            try:
                self._file.flush()
                os.fsync(self._file.fileno())
            except OSError as exc:
                self._fail(f"Transport file finalization failed: {exc}")
            try:
                self._file.close()
            except OSError as exc:
                self._fail(f"Transport file close failed: {exc}")
            with self._lock:
                self._accepting = False
                self._finished = time.time()
                self._sha256 = self._digest.hexdigest()
                self._state = "complete" if self._error is None else "failed"
            try:
                self._persist()
            except OSError as exc:
                self._fail(f"Transport evidence finalization failed: {exc}")
                # Best effort: do not leave a flushed complete=true record after
                # evidence fsync failed. Storage failure can prevent persistence.
                try:
                    self._persist()
                except OSError:
                    pass
            finally:
                try:
                    self._evidence.close()
                except OSError as exc:
                    self._fail(f"Transport evidence close failed: {exc}")

    def close(self):
        with self._lock:
            self._accepting = False
            self._closing = True
        self._worker.join()
