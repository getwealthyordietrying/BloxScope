"""Read-only checks of retained real evidence, separate from test fixtures."""
from bisect import bisect_right
import importlib.metadata
import json
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bloxscope.common import atomic_json, file_hash, now
from bloxscope.pe import PE
from bloxscope.store import Search


def main():
    root = ROOT/".bloxscope"
    search = Search(root)
    try:
        assert search.summary["origin"] == "direct-official-roblox" and not search.summary["fixture_data"]
        queries = {}
        for term in ("gift", "GiftAllowanceView", "GiftEligibility", "100%_[]", "café", "123"):
            start = time.monotonic()
            count, rows = search.page(term)
            queries[term] = {"matching_extracted_occurrences": count, "seconds": round(time.monotonic()-start, 3), "page_size": len(rows)}
        assert search.page("GIFT")[0] == queries["gift"]["matching_extracted_occurrences"]
        samples = []
        for kind in ("executable string", "readable script file", "readable script Source", "script string-table entry", "script instance name"):
            r = search.db.execute("SELECT id FROM strings WHERE type=? AND instr(folded,'gift')>0 LIMIT 1", (kind,)).fetchone()
            if r is None and kind == "executable string":
                r = search.db.execute("SELECT id FROM strings WHERE type=? AND encoding='utf-16-le' AND char_length<200 LIMIT 1", (kind,)).fetchone()
            if r is None:
                continue
            d = search.detail(r["id"])
            raw = search.raw_evidence(d)
            samples.append({"text": d["text"][:300], "type": kind, "file": d["file"]["path"],
                            "file_sha256": d["file"]["sha256"], "build": d["file"]["build"],
                            "location": d["location"], "raw_check": raw,
                            "script_name": d["script"]["name"] if d["script"] else None})
        # Independently check the exact real instruction bytes and address math.
        reference = json.loads((ROOT/"reports"/"real-reference-investigation.json").read_text())
        data = (root/"blobs"/reference["file_sha256"]).read_bytes()
        assert file_hash(root/"blobs"/reference["file_sha256"]) == reference["file_sha256"]
        pe = PE(data)
        ranges = pe.runtime_ranges()
        starts = [a for a, b in ranges]
        proof = []
        import capstone
        for r in reference["verified_decoded_references"]:
            off = r["file_offset"]
            raw = bytes.fromhex(r["bytes"])
            assert data[off:off+len(raw)] == raw
            assert pe.file_to_rva(off)+pe.base == r["instruction_va"]
            ins = next(capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64).disasm(raw, r["instruction_va"]))
            assert raw[:3] == bytes.fromhex("488d15")
            assert r["instruction_va"]+len(raw)+struct.unpack_from("<i", raw, 3)[0] == r["target"]
            target = pe.rva_to_file(r["target"]-pe.base)
            assert data[target:target+reference["byte_length"]].decode(reference["encoding"]) == reference["text"]
            a, b = ranges[bisect_right(starts, r["instruction_va"]-pe.base)-1]
            boundary = False
            f = pe.rva_to_file(a)
            for va, length, mnemonic, operands in capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64).disasm_lite(data[f:f+b-a], pe.base+a):
                if va == r["instruction_va"]:
                    boundary = True
                    break
            assert boundary
            proof.append({"instruction_va": r["instruction_va"], "runtime_range_begin_rva": a,
                          "runtime_range_end_rva": b, "exact_bytes_and_target_verified": True,
                          "boundary_redecoded_from_range_start": True})
        packages = list((root/"packages").glob("*.zip"))
        components = {name: sum(p.stat().st_size for p in (root/name).rglob("*") if p.is_file())
                      for name in ("packages", "blobs", "generations", "analysis", "observations", "staging", "exports")}
        summary = {"verified_at": now(), "origin": "direct-official-roblox", "fixture_data": False,
                   "analysis_version": search.summary["analysis_version"], "generation": search.generation.name,
                   "builds": [{"product": o["product"], "build": o["build"]} for o in search.summary["observations"]],
                   "occurrences": search.summary["occurrences"], "files": search.summary["files"], "scripts": search.summary["scripts"],
                   "coverage_counts": search.summary["coverage_counts"], "partial_or_unsupported_files": search.summary["file_coverage"],
                   "queries": queries, "offset_verified_samples": samples, "real_reference_proof": proof,
                   "capstone_package_version": importlib.metadata.version("capstone"), "capstone_binding_version": capstone.__version__,
                   "capstone_native_version": list(capstone.cs_version()),
                   "selected_package_occurrence_bytes": search.summary["download_package_bytes"],
                   "unique_downloaded_package_bytes": sum(p.stat().st_size for p in packages), "unique_package_count": len(packages),
                   "manifest_declared_output_bytes": search.summary["manifest_declared_output_bytes"],
                   "extracted_file_occurrence_bytes": search.summary["extracted_file_occurrence_bytes"],
                   "active_index_bytes": (search.generation/"index.sqlite3").stat().st_size,
                   "disk_components_bytes": components, "total_store_bytes": sum(p.stat().st_size for p in root.rglob("*") if p.is_file()),
                   "player_reference_blocker": {"exception_directory_rva": 0, "exception_directory_size": 0,
                       "reason": "No supported x64 runtime-function boundaries. No protected bytes were unpacked or executed."}}
        atomic_json(ROOT/"reports"/"real-verification.json", summary)
        print(json.dumps({k: v for k, v in summary.items() if k not in {"partial_or_unsupported_files", "offset_verified_samples"}}, indent=2))
    finally:
        search.close()


if __name__ == "__main__":
    main()
