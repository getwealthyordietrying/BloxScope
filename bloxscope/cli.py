"""Launch, search, open a number, search again."""
import argparse
import json
from pathlib import Path
import sqlite3
import uuid

from .common import ScopeError, atomic_json, atomic_bytes, safe, say, size
from .pe import pointer_page, reference_page
from .prepare import prepare
from .store import Search, active

HELP = "Search any text. Use S <term> for numbers or command words, for example S 123 or S Q. A plain number opens a displayed result. N/P: next/previous page. B: back to Search. H: help. Q: quit. Searches are case-insensitive literal substrings. Each match is one extracted string location; duplicate text stays separate. No relevance cutoff."


def default_store():
    return Path.cwd() / ".bloxscope"


def prompt(text):
    try:
        return input(text)
    except EOFError:
        return "Q"


def detail_lines(d, full=False):
    f, loc, script = d["file"], d["location"], d["script"]
    yield "Found: " + d["type"]
    yield "Exact string:" if full or len(d["text"]) <= 2000 else "String preview (T shows the full exact string):"
    yield d["text"] if full or len(d["text"]) <= 2000 else d["text"][:2000]+"..."
    yield f"Length: {d['char_length']:,} characters; {d['byte_length']:,} encoded bytes. Encoding: {d['encoding']}"
    yield f"Build: {f['product']} {f['build']}. Index observed: {d['index_observed_at']}"
    yield "File: " + f["path"]
    yield "Original file SHA-256: " + f["sha256"]
    yield "Package source URL: " + f["package"]["requested_url"]
    yield "Original download URL: " + f["package"].get("download_url", f["package"]["final_url"])
    yield "Package observed: " + f["package"]["observed_at"] + "; verified: " + f["package"]["verified_at"]
    for key, value in loc.items():
        yield key.replace("_", " ") + ": " + str(value)
    if script:
        yield f"Script: {script['class']} {script['name']}; class ID {script['class_id']}, instance slot {script['instance_index']}"
        yield "Name attribution: " + script["name_attribution"] + ". Slots and names are local to this container/build."
        yield "Original Source SHA-256: " + script["sha256"]
        yield "Source kind: " + script["kind"]
        if script.get("error"):
            yield "Source limit: " + script["error"]
    yield "Origin: " + d["origin"]
    yield "Coverage: " + str(f["coverage"].get("coverage", f["coverage"].get("scope", f["coverage"].get("status"))))
    if f["coverage"].get("opaque_count"):
        yield "Opaque Source properties in this file: " + str(f["coverage"]["opaque_count"])
    yield "Observation: " + d["observations"]
    yield "Limits: " + d["limits"]
    yield "Nearby context (extraction order only; not references):"
    for item in d["nearby_context_not_references"]:
        yield "  " + item["text"][:240]
    yield "Reference status: " + d["reference_analysis"]["status"] + ". " + d["reference_analysis"].get("scope", "")


def export_detail(root, d):
    folder = Path(root) / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / (f"occurrence-{d['id']}-" + uuid.uuid4().hex[:12])
    atomic_json(base.with_suffix(".json"), d)
    # A long fence keeps literal backticks inside evidence from closing it.
    rendered = "\n".join(safe(line) for line in detail_lines(d, full=True))
    raw = json.dumps(d, indent=2, ensure_ascii=True)
    longest = max((len(part) for part in __import__("re").findall(r"`+", rendered+raw)), default=0)
    fence = "`" * max(3, longest+1)
    markdown = f"# BloxScope evidence\n\n{fence}text\n{rendered}\n{fence}\n\nFull evidence and reference classifications:\n\n{fence}json\n{raw}\n{fence}\n"
    atomic_bytes(base.with_suffix(".md"), markdown.encode("utf-8"))
    return base.with_suffix(".json"), base.with_suffix(".md")


def show_refs(result):
    say(f"Experimental reference analysis: {result['count']:,} verified decoded references in this limited coverage. Page {result['page']+1}.")
    say(result["coverage"]["scope"])
    say(f"Incomplete runtime ranges: {result['coverage']['incomplete_ranges']:,}. Uncovered instructions remain unresolved.")
    for item in result["verified_decoded_references"]:
        say(f"  VA {item['instruction_va']:#x}; file offset {item['file_offset']:#x}; target {item['target']:#x}")
        say("  Bytes: " + item["bytes"] + " | " + item["decoded"])
        say("  Validity: " + item["why"])


def details(search, occurrence):
    d = search.detail(occurrence)
    for line in detail_lines(d):
        say(line)
    mode, page, result = None, 0, None
    while True:
        action = prompt("Details: B back, T exact text, R references, C pointer candidates, V raw evidence, E export, H help, Q quit: ").strip().upper()
        if action == "Q":
            return False
        if action in {"B", ""}:
            return True
        try:
            if action == "T":
                say(d["text"])
            elif action == "V":
                d["raw_evidence"] = search.raw_evidence(d)
                say(json.dumps(d, indent=2, ensure_ascii=True))
            elif action == "E":
                paths = export_detail(search.root, d)
                for path in paths:
                    say("Saved: " + str(path.resolve()))
            elif action in {"R", "C", "N", "P"}:
                va = d["location"].get("preferred_va")
                if d["type"] != "executable string" or va is None:
                    say("Script call references are unsupported. Names, slots and table entries are structural evidence only.")
                    continue
                if action in {"R", "C"}:
                    mode, page = action, 0
                elif mode is None:
                    say("Open R or C first. Then N/P pages through that evidence.")
                    continue
                elif action == "N":
                    if result and (page+1)*20 >= result["count"]:
                        say("This is the last reference page.")
                        continue
                    page += 1
                else:
                    page = max(0, page-1)
                original = search.root / "blobs" / d["file"]["sha256"]
                if mode == "R":
                    result = reference_page(original, d["file"]["sha256"], search.root, va, d["byte_length"], page, say)
                    d["reference_analysis"] = {"status": "experimental", "scope": result["coverage"]["scope"], **result}
                    show_refs(result)
                else:
                    # Verify evidence before scanning pointers too.
                    search.raw_evidence(d)
                    result = pointer_page(original.read_bytes(), va, page)
                    d["pointer_candidates"] = result
                    say(f"{result['count']:,} candidate pointer/data patterns; page {page+1}. These are not code references.")
                    for item in result["candidate_pointer_data"]:
                        say(json.dumps(item, ensure_ascii=True))
                say("N/P shows other reference pages. E exports this page with its total count and coverage.")
            else:
                say("B returns to your results. R checks static executable references. C checks pointer candidates. V shows source URLs, hashes and raw evidence. E saves JSON and Markdown. T shows the full string. N/P pages through R or C. " + HELP)
        except (ScopeError, OSError, ValueError) as exc:
            d["reference_analysis"] = {"status": "unsupported-or-unresolved", "scope": str(exc)}
            say(str(exc))


def interactive(search):
    summary = search.summary
    say(f"Search index: {summary['occurrences']:,} extracted string locations. Observed {summary['observed_at']}.")
    for o in summary["observations"]:
        say(f"  {o['product']}: {o['build']}")
    if summary.get("fixture_data"):
        say("SYNTHETIC TEST FIXTURE. These are not Roblox discoveries.")
    say("Enter a search term. H gives help. S <term> searches numbers or command words.")
    term, page, rows, total = None, 0, [], 0
    while True:
        line = prompt("Search: " if term is None else "Result number, new search, N/P, B, H or Q: ")
        command = line.strip().upper()
        explicit = line[:2].upper() == "S "
        if not explicit and command == "Q":
            return
        if not explicit and command == "H":
            say(HELP)
            continue
        if not explicit and command == "B":
            term, rows, page = None, [], 0
            continue
        if not explicit and command in {"N", "P"}:
            if term is None:
                say("Enter a search term first.")
                continue
            if command == "N" and (page+1)*15 < total:
                page += 1
            elif command == "P" and page:
                page -= 1
            else:
                say("There is no other page in that direction.")
                continue
        elif not explicit and line.strip().isdecimal():
            # Bounded before int(): even a pasted very long number stays safe.
            if len(line.strip()) > 12:
                say("For a numeric search, type S followed by the number.")
                continue
            number = int(line.strip())
            slot = number-page*15-1
            if term is None or not 0 <= slot < len(rows):
                say("Enter a number shown on this page. For a numeric search, use S " + line.strip())
                continue
            if not details(search, rows[slot]["id"]):
                return
        else:
            new_term = line[2:] if explicit else line
            if not new_term:
                continue
            term, page = new_term, 0
        say("Searching...")
        total, rows = search.page(term, page)
        say(f"{total:,} matching extracted occurrences for {term!r}. Page {page+1}/{max(1, (total+14)//15)}. Duplicate text has separate locations.")
        for i, row in enumerate(rows, page*15+1):
            text = row["text"]
            preview = text if len(text) <= 150 else text[:150]+"..."
            say(f"{i}. {preview} [{row['type']}]")


def main(argv=None):
    parser = argparse.ArgumentParser(description="BloxScope: launch, prepare, and search public Roblox resource strings.")
    parser.add_argument("--store", type=Path, default=default_store(), help="Separate local evidence and index folder")
    parser.add_argument("--cached", action="store_true", help="Explicitly search the last completed index without a network check")
    parser.add_argument("--prepare-only", action="store_true", help="Prepare the default collection, then exit")
    parser.add_argument("--status", action="store_true", help="Print retained coverage and measured sizes")
    args = parser.parse_args(argv)
    root = args.store.resolve()
    say("BloxScope | local public resource search")
    say("Data folder: " + str(root))
    try:
        if not args.cached and not args.status:
            try:
                prepare(root, say)
            except (ScopeError, OSError, ValueError) as exc:
                say("Preparation failed: " + str(exc))
                if active(root) is None:
                    say("No completed index is available. Retry after the reported problem is fixed.")
                    return 2
                say("The last successful index is still available.")
                if prompt("C to search cached data, Q to quit: ").strip().upper() != "C":
                    return 2
                args.cached = True
        search = Search(root)
        try:
            if args.status:
                say(json.dumps(search.summary, indent=2, ensure_ascii=True))
                say("Current data-folder disk bytes: " + size(sum(p.stat().st_size for p in root.rglob("*") if p.is_file())))
            elif args.prepare_only:
                say("Preparation finished.")
            else:
                if args.cached:
                    say("CACHED DATA. Build and observation dates are shown below. No fresh package observation is claimed.")
                interactive(search)
        finally:
            search.close()
        return 0
    except KeyboardInterrupt:
        say("Stopped. Completed packages and files can be reused. The last successful index stays available.")
        return 130
    except (ScopeError, OSError, ValueError, sqlite3.Error) as exc:
        say("BloxScope could not continue: " + str(exc))
        return 2
