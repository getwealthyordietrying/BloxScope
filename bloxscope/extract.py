"""Bounded ZIP reading and supported string locations."""
from pathlib import PurePosixPath
import re
import stat
import struct
import time
import zipfile
import zlib

from .common import ScopeError, digest
from .container import inspect_scripts
from .container_format import BINARY_RBX
from .pe import PE

MAX_MEMBER = 384 * 1024**2
MAX_ZIP_OUTPUT = 768 * 1024**2
MAX_MEMBERS = 60000
TEXT = {".lua", ".luau", ".json", ".xml", ".txt", ".csv", ".toml", ".rbxp"}
MODELS = {".rbxm", ".rbxl", ".rbxmx", ".rbxlx"}
UTF8 = re.compile(rb"(?:[\x20-\x7e]|[\xc2-\xdf][\x80-\xbf]|\xe0[\xa0-\xbf][\x80-\xbf]|[\xe1-\xec\xee-\xef][\x80-\xbf]{2}|\xed[\x80-\x9f][\x80-\xbf]|\xf0[\x90-\xbf][\x80-\xbf]{2}|[\xf1-\xf3][\x80-\xbf]{3}|\xf4[\x80-\x8f][\x80-\xbf]{2}){4,}")
UTF16_ANCHOR = re.compile(rb"(?:[\x20-\x7e]\x00){4,}")


def archive_members(path, executable=None):
    try:
        with zipfile.ZipFile(path) as z:
            entries = z.infolist()
            if len(entries) > MAX_MEMBERS or sum(e.file_size for e in entries) > MAX_ZIP_OUTPUT:
                raise ScopeError("ZIP exceeds its extraction bounds")
            names = set()
            for e in entries:
                name = e.orig_filename.replace("\\", "/")
                if e.is_dir():
                    # Official ZIPs use /folder/ directory markers. No paths
                    # are created, so directory entries can be ignored safely.
                    continue
                p = PurePosixPath(name.rstrip("/"))
                canonical = p.as_posix()
                if (p.is_absolute() or ".." in p.parts or ":" in name or "\0" in name
                        or canonical != name.rstrip("/") or canonical.casefold() in names
                        or stat.S_ISLNK(e.external_attr >> 16)):
                    raise ScopeError("Unsafe or duplicate ZIP member: " + name)
                names.add(canonical.casefold())
                if e.is_dir():
                    continue
                if e.file_size > MAX_MEMBER or e.flag_bits & 1:
                    raise ScopeError("Unsupported or oversized ZIP member: " + name)
                selected = name == executable if executable else p.suffix.lower() in TEXT | MODELS
                if not selected:
                    yield name, None, e.file_size
                    continue
                with z.open(e) as f:
                    raw = f.read(min(MAX_MEMBER, e.file_size)+1)
                    if len(raw) != e.file_size or f.read(1):
                        raise ScopeError("ZIP member size mismatch")
                yield name, raw, e.file_size
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, zlib.error, struct.error) as exc:
        raise ScopeError("Cannot read ZIP: " + str(exc)) from exc


def lines(raw, encoding="utf-8", base=0):
    text = raw.decode(encoding)
    if any(ord(c) < 32 and c not in "\n\r\t" for c in text):
        raise ScopeError("Unsupported control bytes in text")
    offset = base
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        clean = line.rstrip("\r\n")
        if clean:
            yield clean, offset, len(clean.encode(encoding)), {"line": number}
        offset += len(line.encode(encoding))


def executable_strings(raw):
    """UTF-8 runs; UTF-16LE runs with four ASCII units as an anchor."""
    pe = PE(raw)
    for s in pe.sections:
        if s["executable"]:
            continue
        data = raw[s["raw"]:s["raw"]+s["raw_size"]]
        for m in UTF8.finditer(data):
            text = m.group().decode("utf-8")
            pos, run = m.start(), ""
            for ch in text + "\0":
                if ch.isprintable():
                    run += ch
                else:
                    if len(run) >= 4:
                        offset = s["raw"]+pos
                        yield run, "utf-8", offset, len(run.encode()), {"file_offset": offset, "rva": pe.file_to_rva(offset), "preferred_va": pe.base+pe.file_to_rva(offset), "section": s["name"]}
                    pos += len(run.encode())+len(ch.encode())
                    run = ""
        previous_end = -1
        for m in UTF16_ANCHOR.finditer(data):
            if m.start() < previous_end:
                continue
            a, b = m.start(), m.end()
            while a >= 2:
                unit = int.from_bytes(data[a-2:a], "little")
                if not chr(unit).isprintable() or 0xd800 <= unit <= 0xdfff:
                    break
                a -= 2
            while b+2 <= len(data):
                unit = int.from_bytes(data[b:b+2], "little")
                if not chr(unit).isprintable() or 0xd800 <= unit <= 0xdfff:
                    break
                b += 2
            previous_end = b
            text = data[a:b].decode("utf-16-le")
            offset = s["raw"]+a
            yield text, "utf-16-le", offset, b-a, {"file_offset": offset, "rva": pe.file_to_rva(offset), "preferred_va": pe.base+pe.file_to_rva(offset), "section": s["name"]}


def extract_file(path, raw, emit, script, progress=lambda _: None):
    """emit(text, type, encoding, byte_offset, byte_length, location, script_id)."""
    if raw.startswith(b"MZ"):
        tick, count = time.monotonic(), 0
        for text, encoding, offset, length, loc in executable_strings(raw):
            emit(text, "executable string", encoding, offset, length, loc, None)
            count += 1
            if time.monotonic()-tick > 3:
                progress(f"  extracted {count:,} executable string locations")
                tick = time.monotonic()
        pe = PE(raw)
        return {"status": "supported", "machine": hex(pe.machine), "image_base": pe.base,
                "coverage": "Non-executable PE sections only; UTF-8 printable runs >=4 characters; UTF-16LE BMP printable runs with >=4 consecutive ASCII units. Heuristic string boundaries."}
    if raw.startswith(BINARY_RBX):
        inspected = 0

        def on_script(entry, source, table):
            nonlocal inspected
            inspected += 1
            if inspected % 2000 == 0:
                progress(f"  inspected {inspected:,} script properties")
            sid = script(entry)
            location = {"chunk_file_offset": entry["chunk_offset"],
                        "chunk_uncompressed_source_offset": entry["chunk_uncompressed_source_offset"],
                        "source_file_offset": entry["source_file_offset"],
                        "coordinate": "Source-relative bytes"}
            # The name belongs to this flat slot. It is not a call reference.
            emit(entry["name"], "script instance name", "utf-8", None,
                 len(entry["name"].encode()), {"coordinate": "Name property decompressed chunk bytes",
                 "chunk_file_offset": entry["name_chunk_file_offset"],
                 "chunk_uncompressed_offset": entry["name_chunk_uncompressed_offset"],
                 "file_offset": entry["name_file_offset"], "class_id": entry["class_id"], "instance_index": entry["instance_index"]}, sid)
            if entry["kind"] == "readable-source-property":
                for text, off, length, extra in lines(source):
                    emit(text, "readable script Source", "utf-8", off, length,
                         {**location, **extra, "source_offset": off,
                          "chunk_uncompressed_offset": entry["chunk_uncompressed_source_offset"]+off,
                          "file_offset": None if entry["source_file_offset"] is None else entry["source_file_offset"]+off}, sid)
            elif table is not None:
                for item in table["strings"]:
                    if item["text"]:
                        off = item["source_offset"]
                        emit(item["text"], "script string-table entry", "utf-8", off, item["bytes"],
                             {**location, "string_index": item["index"], "payload_offset": item["offset"],
                              "source_offset": off, "payload_start_in_source": table["payload_offset"],
                              "chunk_uncompressed_offset": entry["chunk_uncompressed_source_offset"]+off,
                              "file_offset": None if entry["source_file_offset"] is None else entry["source_file_offset"]+off}, sid)

        report = inspect_scripts(raw, on_script=on_script)
        report.pop("scripts")
        return report
    if PurePosixPath(path).suffix.lower() in TEXT:
        encoding, base = "utf-8", 0
        if raw.startswith(b"\xef\xbb\xbf"):
            base = 3
        elif raw.startswith(b"\xff\xfe"):
            encoding, base = "utf-16-le", 2
        elif raw.startswith(b"\xfe\xff"):
            encoding, base = "utf-16-be", 2
        try:
            for text, off, length, loc in lines(raw[base:], encoding, base):
                emit(text, "readable script file" if path.lower().endswith((".lua", ".luau")) else "readable text resource",
                     encoding, off, length, {**loc, "file_offset": off, "coordinate": "file bytes"}, None)
        except (UnicodeError, ScopeError) as exc:
            return {"status": "unsupported", "error": str(exc)}
        return {"status": "supported", "coverage": "Nonempty lines, full supported text file; minimum 1 character."}
    return {"status": "unsupported", "error": "Unknown model/container encoding; original bytes retained. No arbitrary headers removed."}
