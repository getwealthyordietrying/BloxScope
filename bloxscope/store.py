"""Immutable search generations and small paged queries."""
import json
from pathlib import Path
import sqlite3

from .common import ScopeError, atomic_bytes, digest, file_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, product TEXT, build TEXT, path TEXT,
 sha256 TEXT, bytes INTEGER, package_json TEXT, coverage_json TEXT,
 UNIQUE(product,build,path));
CREATE TABLE IF NOT EXISTS scripts(id INTEGER PRIMARY KEY, file_id INTEGER, metadata_json TEXT);
CREATE TABLE IF NOT EXISTS strings(id INTEGER PRIMARY KEY, file_id INTEGER, script_id INTEGER,
 type TEXT, encoding TEXT, byte_offset INTEGER, byte_length INTEGER, char_length INTEGER,
 text TEXT, folded TEXT, location_json TEXT);
CREATE INDEX IF NOT EXISTS file_strings ON strings(file_id,id);
CREATE INDEX IF NOT EXISTS script_strings ON strings(script_id,id);
CREATE INDEX IF NOT EXISTS scripts_file ON scripts(file_id,id);
"""


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=5000")
    return db


def put_blob(root, raw):
    h = digest(raw)
    path = Path(root) / "blobs" / h
    if path.exists():
        if path.stat().st_size != len(raw) or file_hash(path) != h:
            raise ScopeError("Original evidence blob is damaged: " + str(path))
    else:
        atomic_bytes(path, raw)
    return h


def active(root):
    root = Path(root)
    pointer = root / "current.json"
    if not pointer.exists():
        return None
    body = json.loads(pointer.read_text())
    key = body["generation"]
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ScopeError("Invalid active generation pointer")
    path = root / "generations" / key
    if not (path / "index.sqlite3").exists() or not (path / "complete.json").exists():
        raise ScopeError("Active index is incomplete")
    return path


class Search:
    def __init__(self, root):
        self.root = Path(root)
        self.generation = active(root)
        if self.generation is None:
            raise ScopeError("There is no completed search index yet")
        path = self.generation / "index.sqlite3"
        self.db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self.summary = json.loads((self.generation / "complete.json").read_text())

    def close(self):
        self.db.close()

    def page(self, term, page=0, page_size=15):
        if not term:
            return 0, []
        folded = term.casefold()
        if getattr(self, "_last_count", (None,))[0] == folded:
            count = self._last_count[1]
        else:
            count = self.db.execute("SELECT count(*) FROM strings WHERE instr(folded,?)>0", (folded,)).fetchone()[0]
            self._last_count = (folded, count)
        if not count:
            return 0, []
        rows = [dict(r) for r in self.db.execute("SELECT id,text,type,encoding FROM strings WHERE instr(folded,?)>0 ORDER BY id LIMIT ? OFFSET ?", (folded, page_size, page*page_size))]
        return count, rows

    def detail(self, occurrence):
        row = self.db.execute("SELECT * FROM strings WHERE id=?", (occurrence,)).fetchone()
        if row is None:
            raise ScopeError("That extracted occurrence is unavailable")
        r = dict(row)
        r["location"] = json.loads(r.pop("location_json"))
        r.pop("folded")
        f = dict(self.db.execute("SELECT * FROM files WHERE id=?", (r["file_id"],)).fetchone())
        f["package"] = json.loads(f.pop("package_json"))
        f["coverage"] = json.loads(f.pop("coverage_json"))
        r["file"] = f
        r["script"] = None
        if r["script_id"] is not None:
            r["script"] = json.loads(self.db.execute("SELECT metadata_json FROM scripts WHERE id=?", (r["script_id"],)).fetchone()[0])
        r["origin"] = self.summary["origin"]
        r["analysis_version"] = self.summary["analysis_version"]
        r["index_observed_at"] = self.summary["observed_at"]
        r["observations"] = "This text was extracted at the recorded location in retained original bytes. Duplicate text is a separate occurrence at each location."
        r["limits"] = "A name or static reference does not prove object identity, execution, feature availability, eligibility, a release plan, or a release date. Script tables are not call references."
        # Neighbor order is the extraction order in this same script or file.
        # Context has no reference classification.
        clause, arg = ("script_id=?", r["script_id"]) if r["script_id"] is not None else ("file_id=?", r["file_id"])
        before = self.db.execute(f"SELECT id,text,type,byte_offset FROM strings WHERE {clause} AND id<? ORDER BY id DESC LIMIT 2", (arg, occurrence)).fetchall()
        after = self.db.execute(f"SELECT id,text,type,byte_offset FROM strings WHERE {clause} AND id>? ORDER BY id LIMIT 2", (arg, occurrence)).fetchall()
        r["nearby_context_not_references"] = [dict(x) for x in reversed(before)] + [dict(x) for x in after]
        r["reference_analysis"] = {"status": "not-run", "scope": "Experimental executable analysis is available with R. Packaged script call references are unsupported."}
        return r

    def raw_evidence(self, detail):
        path = self.root / "blobs" / detail["file"]["sha256"]
        if file_hash(path) != detail["file"]["sha256"]:
            raise ScopeError("Original file hash changed")
        offset = detail["location"].get("file_offset")
        if offset is None:
            from .container_format import chunks
            from .container import decompress, MAX_CHUNK, MAX_TOTAL
            loc = detail["location"]
            chunk_offset = loc.get("chunk_file_offset")
            relative = loc.get("chunk_uncompressed_offset")
            if chunk_offset is None or relative is None:
                raise ScopeError("No supported raw coordinate for this result")
            for c in chunks(path.read_bytes(), MAX_CHUNK, MAX_TOTAL):
                if c["offset"] != chunk_offset:
                    continue
                payload, codec = decompress(c["payload"], c["compressed_bytes"], c["unpacked_bytes"])
                raw = payload[relative:relative+detail["byte_length"]]
                if raw.decode(detail["encoding"]) != detail["text"]:
                    raise ScopeError("Retained chunk bytes do not match this occurrence")
                script = detail["script"]
                if script:
                    # Source and Name can be in different property chunks.
                    if chunk_offset == script["chunk_offset"]:
                        a = script["chunk_uncompressed_source_offset"]
                        if digest(payload[a:a+script["bytes"]]) != script["sha256"]:
                            raise ScopeError("Original Source property hash does not match")
                a = max(0, relative-16)
                return {"original_file": str(path), "file_sha256_verified": True,
                        "coordinate": "decompressed property chunk bytes; not file bytes",
                        "chunk_file_offset": chunk_offset, "codec": codec,
                        "chunk_payload_sha256": digest(payload), "hex_start_chunk_offset": a,
                        "hex_bytes": payload[a:a+min(detail["byte_length"]+32, 512)].hex(),
                        "exact_string_bytes_verified": True, "display_byte_limit": 512}
            raise ScopeError("The recorded property chunk is missing")
        with path.open("rb") as f:
            f.seek(offset)
            exact = f.read(detail["byte_length"])
            if exact.decode(detail["encoding"]) != detail["text"]:
                raise ScopeError("Retained file bytes do not match this occurrence")
            f.seek(max(0, offset-16))
            data = f.read(min(detail["byte_length"]+32, 512))
        return {"original_file": str(path), "file_sha256_verified": True,
                "exact_string_bytes_verified": True,
                "hex_start_file_offset": max(0, offset-16), "hex_bytes": data.hex(),
                "display_byte_limit": 512}
