# Adapted from BloxRadar. See licenses/BloxRadar-MIT.txt.
"""Observed referent/parent arrays provide structural locations, not permanent IDs."""
from collections import Counter
import json

from .common import ScopeError as RadarError


def referents(data, count):
    if count < 0 or count > 100000 or len(data) != 4 * count:
        raise RadarError("Invalid referent-array length/count")
    previous, values = 0, []
    for i in range(count):
        word = int.from_bytes(bytes(data[i + j * count] for j in range(4)), "big")
        previous += (word >> 1) ^ -(word & 1)
        if not -(2**31) <= previous < 2**31:
            raise RadarError("Referent delta overflow")
        values.append(previous)
    return values


def locations(classes, props, parent_data, string_vector):
    if parent_data is None or len(parent_data) < 5 or parent_data[0] != 0:
        raise RadarError("No supported PRNT record; structural identity unavailable")
    count = int.from_bytes(parent_data[1:5], "little")
    if len(parent_data) != 5 + count * 8:
        raise RadarError("PRNT vector length mismatch")
    children = referents(parent_data[5:5 + count * 4], count)
    parents = referents(parent_data[5 + count * 4:], count)
    nodes, slots = {}, {}
    for class_id, klass in classes.items():
        name_prop = props.get((class_id, "Name"))
        if name_prop is None or name_prop["type"] != 1:
            raise RadarError("Missing supported Name vector for hierarchy class")
        names = string_vector(name_prop["values"], klass["count"])
        for index, (ref, raw) in enumerate(zip(klass["refs"], names)):
            if ref < 0 or ref in nodes:
                raise RadarError("Duplicate/negative instance referent")
            try:
                name = raw.decode("utf-8")
            except UnicodeError as exc:
                raise RadarError("Invalid hierarchy name encoding") from exc
            if "\0" in name:
                raise RadarError("NUL in hierarchy name")
            nodes[ref] = (klass["name"], name)
            slots[(class_id, index)] = ref
    if len(children) != len(set(children)) or set(children) != set(nodes):
        raise RadarError("PRNT does not map every instance exactly once")
    parent_map = dict(zip(children, parents))
    if any(parent != -1 and parent not in nodes for parent in parents):
        raise RadarError("Unknown parent referent")
    paths = {}
    for ref in nodes:
        chain, seen, cursor = [], set(), ref
        while cursor != -1 and cursor not in paths:
            if cursor in seen or len(chain) > 256:
                raise RadarError("Cycle or unsupported hierarchy depth")
            seen.add(cursor)
            chain.append(cursor)
            cursor = parent_map[cursor]
        base = paths.get(cursor, ())
        for item in reversed(chain):
            base = base + (nodes[item],)
            paths[item] = base
    counts = Counter(paths.values())
    results = {}
    for slot, ref in slots.items():
        path = paths[ref]
        ambiguous = any(counts[path[:i]] > 1 for i in range(1, len(path) + 1))
        results[slot] = {
            "referent": ref, "hierarchy": [list(p) for p in path],
            "hierarchy_key": None if ambiguous else json.dumps(path, ensure_ascii=True, separators=(",", ":")),
            "identity_status": "ambiguous-hierarchy" if ambiguous else "unique-structural-path",
        }
    return results
