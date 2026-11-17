"""Cross-process limits for outbound Workspace requests."""

import hashlib
import time
from contextlib import contextmanager
from contextvars import ContextVar
from threading import BoundedSemaphore

from sqlalchemy import text

network_slots: ContextVar[object | None] = ContextVar("workspace_network_slots", default=None)
before_write: ContextVar = ContextVar("workspace_before_write", default=None)
request_deadline: ContextVar[float | None] = ContextVar("workspace_request_deadline", default=None)
outbound_user_id: ContextVar[int | None] = ContextVar("workspace_outbound_user", default=None)


class NetworkSlotUnavailable(TimeoutError):
    """The request could not reach the provider because no lease was acquired."""


def _key(*parts):
    value = ":".join(map(str, parts)).encode()
    return int.from_bytes(hashlib.blake2b(value, digest_size=8).digest(), "big", signed=True)


class NetworkSlots:
    def __init__(self, engine, *, background=False):
        self.engine = engine
        self.background = background
        self.local = BoundedSemaphore(2 if background else 8)

    @contextmanager
    def acquire(self, user_id, write=False):
        end = time.monotonic() + 2
        deadline = request_deadline.get()
        if deadline is not None:
            end = min(end, deadline)
        remaining = end - time.monotonic()
        if remaining <= 0 or not self.local.acquire(timeout=remaining):
            raise NetworkSlotUnavailable("Workspace worker network slots busy")
        try:
            if deadline is not None and time.monotonic() >= deadline:
                raise NetworkSlotUnavailable("Workspace request deadline expired while acquiring a slot")
            if self.engine.dialect.name != "postgresql":
                yield
                return
            groups = []
            if self.background:
                groups.append([_key("workspace-background", user_id)])
            groups.append([_key("workspace-user", user_id, write, i) for i in range(1 if write else 4)])
            groups.append([_key("workspace-global", i) for i in (range(14, 16) if self.background else range(16))])
            with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                while time.monotonic() < end:
                    acquired = []
                    try:
                        for group in groups:
                            for key in group:
                                if conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar_one():
                                    acquired.append(key)
                                    break
                            else:
                                break
                        if len(acquired) == len(groups) and time.monotonic() < end:
                            yield
                            return
                    finally:
                        try:
                            for key in reversed(acquired):
                                conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                        except Exception:
                            conn.invalidate()
                            raise
                    remaining = end - time.monotonic()
                    if remaining > 0:
                        time.sleep(min(0.025, remaining))
            raise NetworkSlotUnavailable("Workspace account or global network slots busy")
        finally:
            self.local.release()


@contextmanager
def outbound_slot(user_id, *, write=False):
    limiter = network_slots.get()
    guard = before_write.get() if write else None
    deadline = request_deadline.get()
    if deadline is not None and time.monotonic() >= deadline:
        raise NetworkSlotUnavailable("Workspace request deadline expired before dispatch")
    if limiter is None or user_id is None:
        if guard:
            guard()
        yield
    else:
        with limiter.acquire(user_id, write):
            if guard:
                guard()
            yield
