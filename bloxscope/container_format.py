# Adapted from BloxRadar. See licenses/BloxRadar-MIT.txt.
"""Framing for the observed binary Roblox v0 container layout.

Unknown chunk tags are retained. Header variants, duplicate semantic records,
and unsupported properties must not silently produce successful attribution.
"""
import struct

from .common import ScopeError as RadarError

BINARY_RBX = b"<roblox!\x89\xff\r\n\x1a\n"


def header(data):
    if len(data) < 32 or not data.startswith(BINARY_RBX):
        raise RadarError("Expected a complete binary Roblox container header")
    version, classes, instances = struct.unpack_from("<HII", data, 14)
    if version != 0 or data[24:32] != b"\0" * 8:
        raise RadarError("Unsupported container version or reserved header bytes")
    return {"version": version, "classes": classes, "instances": instances}


def chunks(data, max_chunk=None, max_total=None):
    header(data)
    offset, total = 32, 0
    while offset + 16 <= len(data):
        tag, compressed, unpacked, reserved = struct.unpack_from("<4sIII", data, offset)
        if reserved:
            raise RadarError(f"Nonzero reserved chunk field at {offset}")
        if tag.startswith(b"END") and tag != b"END\0":
            raise RadarError(f"Unsupported END tag at {offset}")
        length = compressed or unpacked
        end = offset + 16 + length
        if end > len(data):
            raise RadarError(f"Truncated chunk at {offset}")
        total += unpacked
        if max_chunk is not None and unpacked > max_chunk:
            raise RadarError(f"Chunk exceeds diagnostic size limit at {offset}")
        if max_total is not None and total > max_total:
            raise RadarError(f"Total diagnostic output limit reached at {offset}")
        payload = data[offset + 16:end]
        if tag == b"END\0":
            if compressed or payload != b"</roblox>":
                raise RadarError(f"Unsupported END payload at {offset}")
            if end != len(data):
                raise RadarError("Trailing data after END chunk")
        yield {"offset": offset, "tag_bytes": tag, "tag": tag.decode("ascii", "replace").rstrip("\0"),
               "compressed_bytes": compressed, "unpacked_bytes": unpacked, "payload": payload}
        if tag == b"END\0":
            return
        offset = end
    raise RadarError("No complete END chunk")


def named_header(payload):
    if len(payload) < 9:
        raise RadarError("Truncated INST/PROP name header")
    class_id, length = struct.unpack_from("<II", payload)
    if length > len(payload) - 9:
        raise RadarError("Truncated INST/PROP name")
    try:
        name = payload[8:8 + length].decode("utf-8")
    except UnicodeError as exc:
        raise RadarError("Invalid UTF-8 INST/PROP name") from exc
    if not name or "\0" in name:
        raise RadarError("Invalid empty/NUL INST/PROP name")
    return class_id, name, payload[8 + length], payload[9 + length:]
