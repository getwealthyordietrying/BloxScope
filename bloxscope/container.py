# Adapted from BloxRadar. See licenses/BloxRadar-MIT.txt.
"""Experimental container diagnostics, NOT a Luau bytecode decoder/decompiler.

Chunk compression and name headers can be probed. inspect_scripts additionally
reads observed Source/Name vectors and leading bytecode string tables. Optional
identity extraction validates the observed parent/name hierarchy; instruction
streams and permanent instance IDs remain uninterpreted.
"""
from collections import Counter
import re
import struct

from .container_format import BINARY_RBX
from .common import ScopeError as RadarError, digest as sha256
from .container_format import chunks as container_chunks, header, named_header

MAX_CHUNK = 128 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
OBSERVED_PLAYER_PREFIX = bytes.fromhex("527a104a5b3209e0")


def lz4_block(data, expected):
    """Bounded raw LZ4 block decode; overlapping matches repeat their window."""
    if expected > MAX_CHUNK:
        raise RadarError("Chunk exceeds diagnostic size limit")
    out, pos = bytearray(), 0

    def length(initial):
        nonlocal pos
        result = initial
        if initial == 15:
            while True:
                if pos >= len(data):
                    raise RadarError("Truncated LZ4 length")
                value = data[pos]
                pos += 1
                result += value
                if value != 255:
                    break
        return result

    while pos < len(data):
        token = data[pos]
        pos += 1
        literal = length(token >> 4)
        if pos + literal > len(data) or len(out) + literal > expected:
            raise RadarError("Invalid LZ4 literal size")
        out.extend(data[pos:pos + literal])
        pos += literal
        if pos == len(data):
            break
        if pos + 2 > len(data):
            raise RadarError("Truncated LZ4 match")
        offset = int.from_bytes(data[pos:pos + 2], "little")
        pos += 2
        match = length(token & 15) + 4
        if offset == 0 or offset > len(out) or len(out) + match > expected:
            raise RadarError("Invalid LZ4 offset or output size")
        window = bytes(out[-offset:])
        out.extend((window * ((match + offset - 1) // offset))[:match])
    if len(out) != expected:
        raise RadarError("LZ4 output does not match declared size")
    return bytes(out)


def decompress(data, compressed, unpacked):
    if unpacked < 0 or unpacked > MAX_CHUNK or len(data) != (compressed or unpacked):
        raise RadarError("Chunk exceeds diagnostic size limit")
    if not compressed:
        return data, "uncompressed"
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        try:
            from compression import zstd  # Python 3.14+, optional for diagnostics
        except ImportError as exc:
            raise RadarError("Zstandard chunk: diagnostics need Python 3.14+ (py -3.14 -m bloxradar ...)") from exc
        decoder = zstd.ZstdDecompressor()
        try:
            result = decoder.decompress(data, max_length=unpacked + 1)
        except zstd.ZstdError as exc:
            raise RadarError("Invalid Zstandard frame: " + str(exc)) from exc
        if len(result) != unpacked or not decoder.eof or decoder.unused_data:
            raise RadarError("Zstandard output length/framing mismatch")
        return result, "zstandard"
    return lz4_block(data, unpacked), "lz4-block"


def string_vector(data, count):
    """Observed type 1/29 property vectors: little-endian length + bytes."""
    pos, result = 0, []
    if count < 0 or count > 100000:
        raise RadarError("Property instance count exceeds diagnostic limit")
    for _ in range(count):
        if pos + 4 > len(data):
            raise RadarError("Truncated property string length")
        size = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        if pos + size > len(data):
            raise RadarError("Truncated property string")
        result.append(data[pos:pos + size])
        pos += size
    if pos != len(data):
        raise RadarError("Unexpected trailing property bytes; unsupported encoding")
    return result


def luau_strings(data):
    """Read ONLY the leading string table, never instructions or functions.

    Format reference: official luau-lang/luau VM/src/lvmload.cpp loadsafe().
    A parsed table is not validation of the remaining bytecode program.
    """
    if len(data) < 3 or not 3 <= data[0] <= 9:
        raise RadarError("Unsupported Luau bytecode header/version")
    version, pos = data[0], 1
    types = data[pos] if version >= 4 else None
    if version >= 4:
        pos += 1
        if not 1 <= types <= 3:
            raise RadarError("Unsupported bytecode types version")

    def varint():
        nonlocal pos
        value = 0
        for shift in range(0, 35, 7):
            if pos >= len(data):
                raise RadarError("Truncated bytecode varint")
            byte = data[pos]
            pos += 1
            if shift == 28 and byte > 15:
                raise RadarError("Bytecode varint overflows uint32")
            value |= (byte & 127) << shift
            if not byte & 128:
                if shift and byte == 0:
                    raise RadarError("Noncanonical bytecode varint")
                return value
        raise RadarError("Oversized bytecode varint")

    count = varint()
    if count > 1000000:
        raise RadarError("Bytecode string count exceeds limit")
    strings = []
    for index in range(count):
        size = varint()
        if pos + size > len(data):
            raise RadarError("Truncated bytecode string table")
        raw = data[pos:pos + size]
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            text = None
        strings.append({"index": index + 1, "offset": pos, "bytes": size,
                        "text": text, "sha256": sha256(raw)})
        pos += size
    return {"bytecode_version": version, "types_version": types,
            "string_count": count, "table_end_offset": pos, "strings": strings}


def script_strings(raw):
    """Support only raw Luau or the exact eight-byte prefix validated in this study.

    Prefix meaning/integrity is unknown. This is not a generic envelope decoder.
    Unknown prefixes are NOT stripped speculatively.
    """
    offset = 8 if raw.startswith(OBSERVED_PLAYER_PREFIX) else 0
    table = luau_strings(raw[offset:])
    table.update(payload_offset=offset, encoding="observed-prefixed-luau" if offset else "raw-luau")
    for string in table["strings"]:
        string.update(source_offset=string["offset"] + offset, offset_basis="bytecode-payload")
    return table


def inspect_scripts(data, query=None, *, include_identity=False, on_script=None):
    """Match flat class/index slots; do not infer hierarchy or code behavior."""
    classes, props, errors = {}, {}, []
    seen_props, invalid_classes = set(), set()
    declared = None
    parent_data, identity_errors = None, []
    try:
        declared = header(data)
        for c in container_chunks(data, MAX_CHUNK, MAX_TOTAL):
            if include_identity and c["tag"] == "PRNT":
                if parent_data is not None:
                    identity_errors.append("Duplicate PRNT record")
                parent_data, _ = decompress(c["payload"], c["compressed_bytes"], c["unpacked_bytes"])
            if c["tag"] not in {"INST", "PROP"}:
                continue
            class_id = None
            try:
                payload, codec = decompress(c["payload"], c["compressed_bytes"], c["unpacked_bytes"])
                class_id, name, value_type, values = named_header(payload)
                if c["tag"] == "INST":
                    if class_id in classes:
                        raise RadarError("Duplicate INST class ID; refusing ambiguous attribution")
                    if value_type not in {0, 1} or len(values) < 4:
                        raise RadarError("Unsupported INST format/count")
                    count = struct.unpack_from("<I", values)[0]
                    if count > 100000 or len(values) != 4 + 4 * count + (count if value_type else 0):
                        raise RadarError("INST count/referent-vector length mismatch")
                    classes[class_id] = {"name": name, "count": count}
                    if include_identity:
                        from .script_identity import referents
                        classes[class_id]["refs"] = referents(values[4:4 + count * 4], count)
                else:
                    key = (class_id, name)
                    if key in seen_props:
                        raise RadarError("Duplicate PROP record; refusing ambiguous attribution")
                    seen_props.add(key)
                    if name in {"Name", "Source"}:
                        props[key] = {"type": value_type, "values": values,
                                      "chunk_offset": c["offset"], "codec": codec,
                                      "values_offset": len(payload) - len(values)}
            except RadarError as exc:
                if class_id is not None:
                    invalid_classes.add(class_id)
                errors.append({"offset": c["offset"], "error": str(exc)})
    except RadarError as exc:
        errors.append({"error": str(exc)})
    if declared is not None:
        if len(classes) != declared["classes"] or sum(c["count"] for c in classes.values()) != declared["instances"]:
            errors.append({"error": "Container declared class/instance counts do not match parsed INST records"})
    for class_id, _ in seen_props:
        if class_id not in classes:
            errors.append({"class_id": class_id, "error": "PROP refers to an unknown INST class"})
            break
    identity_map = {}
    if include_identity:
        try:
            if errors or invalid_classes or identity_errors:
                raise RadarError("Container errors prevent trustworthy structural attribution")
            from .script_identity import locations
            identity_map = locations(classes, props, parent_data, string_vector)
        except RadarError as exc:
            identity_errors.append(str(exc))
    scripts = []
    for class_id, klass in classes.items():
        if klass["name"] not in {"Script", "LocalScript", "ModuleScript"}:
            continue
        if class_id in invalid_classes:
            continue
        source = props.get((class_id, "Source"))
        if not source:
            errors.append({"class_id": class_id, "error": "No decoded Source property for script class"})
            continue
        try:
            if source["type"] not in {1, 29}:
                raise RadarError("Unsupported Source property type: " + str(source["type"]))
            values = string_vector(source["values"], klass["count"])
            names_prop = props.get((class_id, "Name"))
            if not names_prop or names_prop["type"] != 1:
                raise RadarError("Missing/unsupported Name vector; refusing to misattribute scripts")
            names = string_vector(names_prop["values"], klass["count"])
            try:
                decoded_names = [n.decode("utf-8") for n in names]
            except UnicodeError as exc:
                raise RadarError("Invalid UTF-8 instance Name; refusing lossy attribution") from exc
            if any("\0" in n for n in decoded_names):
                raise RadarError("NUL in instance Name; refusing ambiguous attribution")
            vector_pos = 0
            name_pos = 0
            name_counts = Counter(decoded_names)
            for i, raw in enumerate(values):
                value_offset = source["values_offset"] + vector_pos + 4
                name_value_offset = names_prop["values_offset"] + name_pos + 4
                name_pos += 4 + len(names[i])
                vector_pos += 4 + len(raw)
                table = None
                name = decoded_names[i]
                entry = {"class_id": class_id, "instance_index": i, "class": klass["name"],
                         "name": name, "source_property_type": source["type"],
                         "chunk_offset": source["chunk_offset"], "codec": source["codec"],
                         "chunk_uncompressed_source_offset": value_offset,
                         "source_file_offset": source["chunk_offset"] + 16 + value_offset if source["codec"] == "uncompressed" else None,
                         "name_attribution": "ambiguous-name" if name_counts[name] > 1 else "unique-name-in-class",
                         "name_chunk_file_offset": names_prop["chunk_offset"],
                         "name_chunk_uncompressed_offset": name_value_offset,
                         "name_file_offset": names_prop["chunk_offset"] + 16 + name_value_offset if names_prop["codec"] == "uncompressed" else None,
                         "sha256": sha256(raw), "bytes": len(raw),
                         "prefix_hex": raw[:16].hex(), "matches": []}
                if include_identity:
                    entry.update(identity_map.get((class_id, i), {"identity_status": "unavailable", "hierarchy_key": None}))
                if query and query.casefold() in name.casefold():
                    entry["matches"].append({"kind": "instance-name", "text": name})
                if source["type"] == 1:
                    try:
                        text = raw.decode("utf-8")
                        if any(ord(c) < 32 and c not in "\t\r\n" for c in text):
                            raise ValueError("Non-text source")
                        entry["kind"] = "readable-source-property"
                        if query:
                            entry["matches"].extend({"kind": "source-line", "line": j + 1, "text": line}
                                                    for j, line in enumerate(text.splitlines()) if query.casefold() in line.casefold())
                    except (UnicodeError, ValueError):
                        entry.update(kind="opaque-source-property", error="Source is not supported UTF-8 text")
                else:
                    try:
                        table = script_strings(raw)
                        entry.update(kind="luau-string-table", bytecode_version=table["bytecode_version"],
                                     types_version=table["types_version"], string_count=table["string_count"],
                                     encoding=table["encoding"], payload_offset=table["payload_offset"],
                                     bytecode_payload_sha256=sha256(raw[table["payload_offset"]:]))
                        if query:
                            entry["matches"].extend({"kind": "bytecode-string", **string} for string in table["strings"]
                                                    if string["text"] is not None and query.casefold() in string["text"].casefold())
                    except RadarError as exc:
                        entry.update(kind="opaque-source-property", error=str(exc))
                if on_script is not None:
                    on_script(entry, raw, table)
                scripts.append(entry)
        except RadarError as exc:
            errors.append({"class_id": class_id, "error": str(exc)})
    opaque = sum(s["kind"] == "opaque-source-property" for s in scripts)
    expected = sum(c["count"] for c in classes.values() if c["name"] in {"Script", "LocalScript", "ModuleScript"})
    return {"status": "partial" if errors or opaque else "complete-script-metadata",
            "scope": "Experimental flat script metadata. Bytecode strings are literals/names, NOT reconstructed code or proof of behavior. No instruction decoding/decompilation.",
            "query": query, "script_count": len(scripts), "declared_script_count": expected,
            "uninspected_script_count": expected - len(scripts), "opaque_count": opaque,
            "kinds": dict(Counter(s["kind"] for s in scripts)),
            "matched_scripts": sum(bool(s["matches"]) for s in scripts),
            "identity_errors": identity_errors,
            "errors": errors, "scripts": scripts}
