"""Checkpoint each verified package and each completed file. Promote at the end."""
import json
from pathlib import Path

from . import ANALYSIS_VERSION
from .common import ScopeError, atomic_json, digest, file_hash, now, process_lock, size
from .extract import archive_members, extract_file
from .sources import HTTP, EXECUTABLES, discover, package
from .store import SCHEMA, active, connect, put_blob


def prepare(root, progress=print, http=None):
    root = Path(root).resolve()
    with process_lock(root):
        attempt = root / "observations" / now().replace(":", "-")
        progress("Checking official Player and Studio versions...")
        atomic_json(root / "progress.json", {"stage": "discovery", "started_at": now()})
        try:
            observations = discover(http or HTTP(), attempt, progress)
            return prepare_observations(root, observations, progress, http or HTTP(), attempt)
        except BaseException as exc:
            atomic_json(root / "progress.json", {"stage": "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                                                "at": now(), "error": str(exc), "last_success_preserved": True})
            raise


def prepare_observations(root, observations, progress, http, attempt):
    root, attempt = Path(root), Path(attempt)
    plan = {"observations": observations, "analysis_version": ANALYSIS_VERSION}
    atomic_json(attempt / "selection.json", plan)
    identity = [{"product": o["product"], "build": o["build"], "manifest": o["manifest_sha256"], "selected": o["selected"], "origin": http.origin} for o in observations]
    key = digest(json.dumps([identity, ANALYSIS_VERSION], sort_keys=True).encode())
    complete = root / "generations" / key
    expected = sum(p["compressed_size"] for o in observations for p in o["selected"])
    unpacked = sum(p["unpacked_size"] for o in observations for p in o["selected"])
    unique_packages = {p["md5"]: p for o in observations for p in o["selected"]}
    unique_bytes = sum(p["compressed_size"] for p in unique_packages.values())
    missing = sum(p["compressed_size"] for p in unique_packages.values() if not (root / "packages" / (p["md5"]+".zip")).exists())
    progress("Selected package bytes across both products: " + size(expected))
    progress("Unique package download bytes: " + size(unique_bytes))
    progress("ZIP declared output: " + size(unpacked) + ". Only supported file types and the main executables are extracted.")
    progress("Expected network package bytes this run: " + size(missing) + " (cache is verified before use).")
    for o in observations:
        progress(f"{o['product']}: {o['build']}; {len(o['selected'])} selected packages.")
        if o["absent"]:
            progress("  Not present in manifest: " + ", ".join(o["absent"]))
    if (complete / "complete.json").exists():
        # Verify evidence, even if the observed build did not change.
        progress("Same builds. Checking retained evidence and reusing the completed index...")
        with_db = connect(complete / "index.sqlite3")
        try:
            for f in with_db.execute("SELECT sha256,bytes FROM files"):
                path = root / "blobs" / f["sha256"]
                if not path.exists() or path.stat().st_size != f["bytes"] or file_hash(path) != f["sha256"]:
                    raise ScopeError("Retained original file is missing or damaged: " + f["sha256"])
        finally:
            with_db.close()
        for o in observations:
            for p in o["selected"]:
                package(http, root / "packages", o["build"], p, progress)
        atomic_json(root / "current.json", {"generation": key, "checked_at": now()})
        atomic_json(root / "progress.json", {"stage": "ready", "generation": key, "cache_reused": True, "at": now()})
        return complete
    stage = root / "staging" / key
    stage.mkdir(parents=True, exist_ok=True)
    atomic_json(stage / "selection.json", plan)
    db = connect(stage / "index.sqlite3")
    db.executescript(SCHEMA)
    previous = active(root)
    can_reuse = False
    if previous is not None:
        prior_summary = json.loads((previous / "complete.json").read_text())
        can_reuse = prior_summary["analysis_version"] == ANALYSIS_VERSION and prior_summary["origin"] == http.origin
        if can_reuse:
            db.execute("ATTACH DATABASE ? AS prior", (str(previous / "index.sqlite3"),))
            db.execute("CREATE TEMP TABLE script_map(old_id INTEGER PRIMARY KEY,new_id INTEGER)")
    reused_files = 0
    try:
        for o in observations:
            for p in o["selected"]:
                label = f"{o['product']}/{p['name']}"
                atomic_json(root / "progress.json", {"stage": "package", "label": label, "generation": key, "at": now()})
                progress("Preparing " + label + " (" + size(p["compressed_size"]) + ")")
                archive, metadata = package(http, root / "packages", o["build"], p, progress)
                atomic_json(stage / (o["product"] + "-" + p["name"] + ".json"), metadata)
                main = EXECUTABLES[o["product"]][1] if p["name"] == EXECUTABLES[o["product"]][0] else None
                seen_main, inventory, extracted = False, [], 0
                for member, raw, declared_size in archive_members(archive, main):
                    internal = p["name"] + "/" + member
                    inventory.append({"path": member, "bytes": declared_size, "extracted": raw is not None})
                    if raw is None:
                        continue
                    extracted += 1
                    if main and member == main:
                        seen_main = True
                    h = put_blob(root, raw)
                    done = db.execute("SELECT id,sha256 FROM files WHERE product=? AND build=? AND path=?", (o["product"], o["build"], internal)).fetchone()
                    if done is not None:
                        if done["sha256"] != h:
                            raise ScopeError("Checkpoint file hash mismatch")
                        continue
                    with db:
                        fid = db.execute("INSERT INTO files(product,build,path,sha256,bytes,package_json,coverage_json) VALUES(?,?,?,?,?,?,?)",
                                         (o["product"], o["build"], internal, h, len(raw), json.dumps(metadata), "{}")).lastrowid
                        prior = db.execute("SELECT id,coverage_json FROM prior.files WHERE product=? AND path=? AND sha256=?", (o["product"], internal, h)).fetchone() if can_reuse else None
                        if prior is not None:
                            db.execute("DELETE FROM script_map")
                            prior_scripts = db.execute("SELECT id,metadata_json FROM prior.scripts WHERE file_id=?", (prior["id"],)) if raw.startswith(b"<roblox!") else ()
                            for old in prior_scripts:
                                sid = db.execute("INSERT INTO scripts(file_id,metadata_json) VALUES(?,?)", (fid, old["metadata_json"])).lastrowid
                                db.execute("INSERT INTO script_map VALUES(?,?)", (old["id"], sid))
                            db.execute("INSERT INTO strings(file_id,script_id,type,encoding,byte_offset,byte_length,char_length,text,folded,location_json) SELECT ?,m.new_id,s.type,s.encoding,s.byte_offset,s.byte_length,s.char_length,s.text,s.folded,s.location_json FROM prior.strings s LEFT JOIN script_map m ON m.old_id=s.script_id WHERE s.file_id=? ORDER BY s.id", (fid, prior["id"]))
                            db.execute("UPDATE files SET coverage_json=? WHERE id=?", (prior["coverage_json"], fid))
                            reused_files += 1
                            if reused_files % 200 == 0:
                                progress(f"  reused extraction for {reused_files:,} unchanged files")
                            continue
                        pending = []

                        def emit(text, kind, encoding, offset, length, location, sid):
                            if not text:
                                return
                            pending.append((fid, sid, kind, encoding, offset, length, len(text), text, text.casefold(), json.dumps(location, separators=(",", ":"))))
                            if len(pending) >= 2000:
                                flush()

                        def flush():
                            db.executemany("INSERT INTO strings(file_id,script_id,type,encoding,byte_offset,byte_length,char_length,text,folded,location_json) VALUES(?,?,?,?,?,?,?,?,?,?)", pending)
                            pending.clear()

                        def script(entry):
                            return db.execute("INSERT INTO scripts(file_id,metadata_json) VALUES(?,?)", (fid, json.dumps(entry, separators=(",", ":")))).lastrowid

                        if main or raw.startswith(b"<roblox!"):
                            progress("  indexing " + internal)
                        coverage = extract_file(member, raw, emit, script, progress)
                        flush()
                        if coverage.get("errors"):
                            raise ScopeError("Container extraction failed; old index stays active: " + internal + ": " + str(coverage["errors"]))
                        db.execute("UPDATE files SET coverage_json=? WHERE id=?", (json.dumps(coverage), fid))
                    if extracted % 200 == 0:
                        progress(f"  {extracted:,} files checked in {p['name']}")
                    atomic_json(root / "progress.json", {"stage": "indexing", "last_completed_file": internal, "generation": key, "at": now()})
                atomic_json(stage / (o["product"] + "-" + p["name"] + "-inventory.json"), inventory)
                if main and not seen_main:
                    raise ScopeError("Main executable is missing from the selected package: " + main)
        count = db.execute("SELECT count(*) FROM strings").fetchone()[0]
        if count == 0:
            raise ScopeError("No supported strings were extracted. The old index stays active.")
        summary = {"origin": http.origin, "fixture_data": http.origin != "direct-official-roblox", "analysis_version": ANALYSIS_VERSION,
                   "observed_at": now(), "generation": key, "observations": observations,
                   "occurrences": count, "files": db.execute("SELECT count(*) FROM files").fetchone()[0],
                   "scripts": db.execute("SELECT count(*) FROM scripts").fetchone()[0],
                   "download_package_bytes": expected, "manifest_declared_output_bytes": unpacked,
                   "unique_package_bytes": unique_bytes, "reused_file_extractions": reused_files,
                   "extracted_file_occurrence_bytes": db.execute("SELECT sum(bytes) FROM files").fetchone()[0],
                   "coverage_counts": [dict(r) for r in db.execute("SELECT type,count(*) AS count FROM strings GROUP BY type")],
                   "file_coverage": [dict(r) for r in db.execute("SELECT product,path,coverage_json FROM files WHERE coverage_json LIKE '%partial%' OR coverage_json LIKE '%unsupported%'")]}
        db.execute("INSERT OR REPLACE INTO metadata VALUES('analysis_version',?)", (ANALYSIS_VERSION,))
        db.commit()
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ScopeError("Completed index did not pass SQLite verification")
    finally:
        db.close()
    atomic_json(stage / "complete.json", summary)
    complete.parent.mkdir(parents=True, exist_ok=True)
    stage.replace(complete)
    atomic_json(root / "current.json", {"generation": key, "checked_at": now()})
    atomic_json(root / "progress.json", {"stage": "ready", "generation": key, "at": now()})
    progress(f"Ready: {count:,} extracted occurrences. Original packages and files are retained.")
    return complete
