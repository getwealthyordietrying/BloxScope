"""Small storage and terminal helpers."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import unicodedata


class ScopeError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_hash(path, algorithm="sha256", on_bytes=None):
    h = hashlib.new(algorithm)
    done = 0
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
            done += len(block)
            if on_bytes:
                on_bytes(done)
    return h.hexdigest()


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with temp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_bytes(path, (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode())


def safe(value):
    out = []
    for ch in str(value):
        if not ch.isprintable() or unicodedata.category(ch) in {"Cf", "Cs"}:
            out.append(ascii(ch)[1:-1])
        else:
            out.append(ch)
    return "".join(out)


def say(value="", *, tone=None):
    from .terminal import decorate, finish_active
    finish_active()
    text = safe(value)
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    print(decorate(text.encode(encoding, "backslashreplace").decode(encoding), tone))


def size(value):
    return f"{value:,} bytes ({value / 1024**2:.1f} MiB)"


@contextmanager
def process_lock(root):
    """The OS releases this lock after a crash. The lock file stays."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    f = (root / "prepare.lock").open("a+b")
    try:
        f.seek(0, 2)
        if not f.tell():
            f.write(b"0")
            f.flush()
        f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ScopeError("Another process is preparing this store. Try again after it ends.") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)
    finally:
        f.close()
