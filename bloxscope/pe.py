"""Small PE32+ reader and conservative AMD64 reference inspection."""
import importlib.metadata
import json
from pathlib import Path
import re
import sqlite3
import struct

from .common import ScopeError, atomic_json, file_hash, now, process_lock

REFERENCE_VERSION = "amd64-unwind-linear-1"


class PE:
    def __init__(self, data):
        self.data = data
        try:
            if data[:2] != b"MZ":
                raise ScopeError("Not a PE image")
            off = struct.unpack_from("<I", data, 0x3c)[0]
            if data[off:off+4] != b"PE\0\0":
                raise ScopeError("Invalid PE signature")
            self.machine, n = struct.unpack_from("<HH", data, off+4)
            opt_size = struct.unpack_from("<H", data, off+20)[0]
            opt = off + 24
            if struct.unpack_from("<H", data, opt)[0] != 0x20b or opt_size < 144:
                raise ScopeError("Only PE32+ images are supported")
            self.base = struct.unpack_from("<Q", data, opt+24)[0]
            if self.base > 2**63-2**32:
                raise ScopeError("Image base is outside the supported address range")
            self.entry = struct.unpack_from("<I", data, opt+16)[0]
            self.exception_rva, self.exception_size = struct.unpack_from("<II", data, opt+112+3*8)
            self.sections = []
            if not 1 <= n <= 96:
                raise ScopeError("Invalid PE section count")
            for i in range(n):
                p = opt + opt_size + i*40
                name, virtual_size, rva, raw_size, raw = struct.unpack_from("<8sIIII", data, p)
                flags = struct.unpack_from("<I", data, p+36)[0]
                if raw + raw_size > len(data):
                    raise ScopeError("Truncated PE section")
                s = {"name": name.rstrip(b"\0").decode("ascii", "backslashreplace"),
                     "rva": rva, "virtual_size": virtual_size, "raw": raw,
                     "raw_size": raw_size, "executable": bool(flags & 0x20000000)}
                for other in self.sections:
                    if raw_size and other["raw_size"] and max(raw, other["raw"]) < min(raw+raw_size, other["raw"]+other["raw_size"]):
                        raise ScopeError("Overlapping PE file sections")
                    if max(rva, other["rva"]) < min(rva+max(raw_size, virtual_size), other["rva"]+max(other["raw_size"], other["virtual_size"])):
                        raise ScopeError("Overlapping PE RVA sections")
                self.sections.append(s)
        except (struct.error, IndexError) as exc:
            raise ScopeError("Truncated PE headers") from exc

    def section_at_rva(self, rva):
        return next((s for s in self.sections if s["rva"] <= rva < s["rva"]+s["raw_size"]), None)

    def rva_to_file(self, rva):
        s = self.section_at_rva(rva)
        return None if s is None else s["raw"] + rva - s["rva"]

    def file_to_rva(self, offset):
        for s in self.sections:
            if s["raw"] <= offset < s["raw"]+s["raw_size"]:
                return s["rva"] + offset - s["raw"]
        return None

    def runtime_ranges(self):
        if self.machine != 0x8664:
            raise ScopeError("Reference analysis supports AMD64 only")
        start = self.rva_to_file(self.exception_rva)
        if start is None or self.exception_size % 12 or self.exception_size > 32*1024**2:
            raise ScopeError("No supported x64 exception directory")
        if self.rva_to_file(self.exception_rva + self.exception_size-1) != start+self.exception_size-1:
            raise ScopeError("Exception directory is not contiguous")
        result = []
        last = -1
        for p in range(start, start+self.exception_size, 12):
            a, b, unwind = struct.unpack_from("<III", self.data, p)
            if not (a or b or unwind):
                continue
            s = self.section_at_rva(a)
            up = self.rva_to_file(unwind)
            if (a < last or b <= a or b-a > 4*1024**2 or not s or not s["executable"]
                    or self.rva_to_file(b-1) != self.rva_to_file(a)+b-a-1
                    or up is None or self.data[up] & 7 not in (1, 2)):
                raise ScopeError("Unsupported/overlapping x64 runtime-function ranges")
            result.append((a, b))
            last = b
        if not result:
            raise ScopeError("No runtime-function ranges")
        return result


def build_references(data, db_path, progress=lambda _: None):
    """Linear decoding starts only at validated unwind range starts.

    No resynchronization after an invalid instruction. These are static decoded
    references, not a control-flow/reachability or function recovery claim.
    """
    try:
        import capstone
        from capstone.x86_const import X86_OP_MEM, X86_OP_IMM, X86_REG_RIP
    except ImportError as exc:
        raise ScopeError("Experimental references need optional capstone==5.0.9. See README.") from exc
    pe = PE(data)
    ranges = pe.runtime_ranges()
    lite = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    detailed = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    detailed.detail = True
    db = sqlite3.connect(db_path)
    stats = {"analysis_version": REFERENCE_VERSION, "backend": f"Capstone {capstone.__version__}",
             "package_version": importlib.metadata.version("capstone"),
             "binding_version": capstone.__version__, "native_version": list(capstone.cs_version()),
             "status": "experimental", "range_count": len(ranges), "decoded_bytes": 0,
             "incomplete_ranges": 0, "references": 0, "observed_at": now(),
             "scope": "AMD64 PE32+ unwind ranges, linear decoded boundaries; RIP-relative LEA/MOV and MOV immediate VA. No reachability claim."}
    try:
        db.executescript("CREATE TABLE refs(target INTEGER, instruction_va INTEGER, file_offset INTEGER, bytes TEXT, decoded TEXT, why TEXT); CREATE INDEX targets ON refs(target);")
        for i, (a, b) in enumerate(ranges):
            p = pe.rva_to_file(a)
            end = a
            for va, length, mnemonic, operands in lite.disasm_lite(data[p:p+b-a], pe.base+a):
                end = va-pe.base+length
                stats["decoded_bytes"] += length
                if mnemonic not in {"lea", "mov", "movabs"}:
                    continue
                if "rip" not in operands and not re.search(r", 0x[0-9a-f]+$", operands):
                    continue
                offset = p+va-pe.base-a
                raw = data[offset:offset+length]
                ins = next(detailed.disasm(raw, va, count=1), None)
                if ins is None or ins.size != length:
                    continue
                for operand in ins.operands:
                    target = None
                    if operand.type == X86_OP_MEM and operand.mem.base == X86_REG_RIP and operand.mem.index == 0:
                        target = va+length+operand.mem.disp
                        why = "Capstone decoded RIP-relative operand at a boundary reached from the x64 unwind range start. Target = next instruction VA + signed displacement."
                    elif operand.type == X86_OP_IMM and mnemonic in {"mov", "movabs"}:
                        target = operand.imm
                        why = "Capstone decoded MOV immediate at a boundary reached from the x64 unwind range start; the immediate maps into this image."
                    if target is None or pe.rva_to_file(target-pe.base) is None:
                        continue
                    db.execute("INSERT INTO refs VALUES(?,?,?,?,?,?)", (target, va, offset, raw.hex(), mnemonic+" "+operands, why))
                    stats["references"] += 1
            if end != b:
                stats["incomplete_ranges"] += 1
            if i % 5000 == 0:
                progress(f"Reference analysis: {i:,}/{len(ranges):,} runtime ranges")
        db.commit()
        return stats
    finally:
        db.close()


def reference_page(path, expected_hash, store, start_va, byte_length, page=0, progress=lambda _: None):
    """Analysis caches are separate from the immutable search generation."""
    if file_hash(path) != expected_hash:
        raise ScopeError("Original executable hash changed; references were not read")
    root = Path(store) / "analysis" / (expected_hash + "-" + REFERENCE_VERSION)
    root.mkdir(parents=True, exist_ok=True)
    db_path, meta_path = root / "references.sqlite3", root / "coverage.json"
    with process_lock(root):
        if not db_path.exists() or not meta_path.exists():
            temporary = root / "building.sqlite3"
            temporary.unlink(missing_ok=True)
            stats = build_references(Path(path).read_bytes(), temporary, progress)
            temporary.replace(db_path)
            atomic_json(meta_path, stats)
    db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        args = (start_va, start_va + byte_length)
        count = db.execute("SELECT count(*) FROM refs WHERE target>=? AND target<?", args).fetchone()[0]
        rows = [dict(r) for r in db.execute("SELECT * FROM refs WHERE target>=? AND target<? ORDER BY instruction_va LIMIT 20 OFFSET ?", (*args, page*20))]
        for row in rows:
            row["target_byte_offset_in_string"] = row["target"]-start_va
        return {"coverage": json.loads(meta_path.read_text()), "count": count, "page": page, "verified_decoded_references": rows}
    finally:
        db.close()


def pointer_page(data, start_va, page=0):
    """Exact 64-bit pointer patterns are candidates, never instructions."""
    pe = PE(data)
    needle = struct.pack("<Q", start_va)
    count, rows = 0, []
    for s in pe.sections:
        if s["executable"]:
            continue
        p, end = s["raw"], s["raw"]+s["raw_size"]
        while (p := data.find(needle, p, end)) != -1:
            if page*20 <= count < (page+1)*20:
                rows.append({"file_offset": p, "rva": pe.file_to_rva(p), "bytes": needle.hex(),
                             "target_va": start_va, "classification": "candidate-pointer-data",
                             "why": "Exact little-endian 64-bit VA bytes in a non-executable section; not proof of use."})
            count += 1
            p += 1
    return {"count": count, "page": page, "candidate_pointer_data": rows}
