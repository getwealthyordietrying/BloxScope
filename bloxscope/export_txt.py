"""Stream an exact-size preview, then publish a confirmed Desktop export."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid

from .common import ScopeError, now, safe
from .terminal import report

COVERAGE = (
    "Selected public Windows Player and Studio packages only; not the whole distribution.",
    "Executable strings: non-executable PE sections by flags; UTF-8 printable runs >=4 characters; "
    "UTF-16LE BMP runs with >=4 consecutive ASCII units. Byte-string boundaries are heuristic.",
    "Supported loose text: UTF-8 or BOM-marked UTF-16LE/BE, nonempty lines >=1 character.",
    "Binary v0 containers: supported Name properties, readable type-1 UTF-8 Source lines and "
    "type-29 raw/exact-prefix Luau tables. Nonempty supported entries only; unknown encodings stay opaque.",
    "Luau headers 3-9 and supported types 1-3; only exact prefix 527a104a5b3209e0 is recognized. "
    "This is string-table extraction, not bytecode validation or decompilation.",
    "File offsets, RVAs, preferred VAs, decompressed chunk offsets, Source-relative offsets and "
    "bytecode-payload-relative offsets are separate coordinates. None means unavailable.",
)


def _known_desktop():
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    guid = (ctypes.c_ubyte*16).from_buffer_copy(uuid.UUID("b4bfcc3a-db2c-424c-b029-7fe99a87c641").bytes_le)
    result = ctypes.c_void_p()
    shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                                         ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    try:
        status = shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(result))
        if status != 0 or not result.value:
            raise OSError("Windows could not resolve the Desktop folder")
        return Path(ctypes.wstring_at(result))
    finally:
        if result.value:
            ole.CoTaskMemFree(result)


def _registry_desktop():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
        value, _ = winreg.QueryValueEx(key, "Desktop")
    return Path(ntpath.expandvars(value))


def desktop_directory():
    if sys.platform == "win32":
        try:
            path = _known_desktop()
        except OSError:
            try:
                path = _registry_desktop()
            except OSError as exc:
                raise ScopeError("Cannot find your Windows Desktop folder. No export was saved.") from exc
    else:
        path = Path.home()/"Desktop"
    if not path.is_dir():
        raise ScopeError("Desktop folder is unavailable: " + str(path))
    return path.resolve()


def destination_for(desktop, term, *, advanced=False):
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", term)[:60].strip("._-") or "search"
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d-%H%M%S")
    for _ in range(32):
        path = Path(desktop)/f"BloxScope-{'advanced' if advanced else 'basic'}-{name}-{stamp}-{uuid.uuid4().hex[:8]}.txt"
        if not path.exists():
            return path
    raise ScopeError("Cannot choose a free export filename. No existing file was changed.")


def quoted(text):
    return safe(json.dumps(text, ensure_ascii=False))


def result_lines(number, row):
    yield f"Result {number} | occurrence {row['id']}"
    yield "Exact string: " + quoted(row["text"])
    yield "Source type: " + row["type"]
    yield f"Product/build: {row['product']} {row['build']}"
    yield "File: " + row["path"]
    yield "Original file SHA-256: " + row["sha256"]
    yield f"Encoding: {row['encoding']}; characters: {row['char_length']}; encoded bytes: {row['byte_length']}"
    for key, value in json.loads(row["location_json"]).items():
        yield key.replace("_", " ") + ": " + str(value)
    if row["metadata_json"] is not None:
        script = json.loads(row["metadata_json"])
        yield f"Script: {script['class']} {script['name']}"
        yield f"Class ID: {script['class_id']}; instance slot: {script['instance_index']}"
        yield "Name attribution: " + script["name_attribution"] + "; local to this container/build; not a permanent identity."
        yield "Source kind: " + script["kind"]
        yield "Original Source SHA-256: " + script["sha256"]
        if script.get("error"):
            yield "Source limit: " + script["error"]
    package = json.loads(row["package_json"])
    coverage = json.loads(row["coverage_json"])
    yield "Requested package URL: " + package["requested_url"]
    yield "Original download URL: " + package.get("download_url", package["final_url"])
    yield "Original package observed: " + package["observed_at"]
    yield "Package verification: " + package["verified_at"]
    yield "Acquisition cache hit: " + str(package["cache_hit"]) + "; this is not a fresh HTTP response from the requested URL."
    yield "File coverage: " + json.dumps(coverage, ensure_ascii=False, separators=(",", ":"))
    yield ""


@dataclass
class Preview:
    stream: object
    count: int
    bytes: int


@contextmanager
def preview(search, term, *, cached=False, progress=lambda _: None, advanced=False):
    count = search.count(term)
    summary = search.summary
    with tempfile.TemporaryFile(mode="w+b") as stream:
        def write(line):
            stream.write((safe(line)+"\n").encode("utf-8"))

        if advanced:
            write("BloxScope search results")
            write("Search term: " + quoted(term))
            write(f"Matching extracted occurrences: {count}")
            write("Export timestamp (UTC): " + now())
            write("Index generation: " + search.generation.name)
            write("Index observed: " + summary["observed_at"])
            write("Analysis version: " + summary["analysis_version"])
            write("Origin: " + summary["origin"])
            write("Fixture data: " + str(summary.get("fixture_data", False)))
            write("Cached mode (no successful refresh this session): " + ("Yes" if cached else "No"))
            write("All results come from this one retained index generation.")
            for observation in summary["observations"]:
                write(f"Observed build: {observation['product']} {observation['build']}")
                for field in ("version_source", "manifest_source"):
                    source = observation[field]
                    write(f"  {field.replace('_', ' ')}: {source['requested_url']}; observed {source['observed_at']}")
                write("  Selected packages: " + ", ".join(p["name"] for p in observation["selected"]))
                write("  Absent packages: " + ", ".join(observation["absent"]))
                write("  Omitted packages: " + ", ".join(observation["omitted"]))
            write("")
            write("Coverage and limits:")
            for line in COVERAGE:
                write(line)
            write("Counts are extracted string records, not unique texts or repetitions within a record. Duplicate locations are retained.")
            write("Names and static references do not establish object identity, execution, feature behavior, availability or release plans.")
            write("No executable reference analysis was run for this export. Script call references are unsupported.")
            for file in summary.get("file_coverage", []):
                write("Coverage gap: " + json.dumps(file, ensure_ascii=False))
            write("")
        report(progress, "Preparing TXT export", 0, count, "matches")
        written = 0
        for written, row in enumerate(search.export_rows(term), 1):
            if advanced:
                for line in result_lines(written, row):
                    write(line)
            else:
                write(f"{written}. " + safe(row["text"]))
            if written % 500 == 0 or written == count:
                report(progress, "Preparing TXT export", written, count, "matches")
        if written != count:
            raise ScopeError("Result count changed while preparing the export. No export was saved.")
        stream.flush()
        length = stream.tell()
        stream.seek(0)
        yield Preview(stream, count, length)


def publish(preview, destination):
    """Desktop staging starts only after confirmation; promotion never replaces."""
    destination = Path(destination)
    fd, temporary = tempfile.mkstemp(prefix=".BloxScope-", suffix=".part", dir=destination.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as output:
            preview.stream.seek(0)
            shutil.copyfileobj(preview.stream, output, length=1024**2)
            output.flush()
            os.fsync(output.fileno())
        if temporary.stat().st_size != preview.bytes:
            raise ScopeError("TXT write size did not match the preview. No final export was saved.")
        if os.name == "nt":
            # Windows rename fails when the destination already exists.
            os.rename(temporary, destination)
        else:
            os.link(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
