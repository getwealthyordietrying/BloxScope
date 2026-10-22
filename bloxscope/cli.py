"""Launch, search, open a number, search again."""
import argparse
import json
import re
from pathlib import Path
import sqlite3
import uuid

from .common import ScopeError, atomic_json, atomic_bytes, safe, say, size
from .pe import pointer_page, reference_page
from .prepare import prepare
from .store import Search, active
from .export_txt import desktop_directory, destination_for, preview, publish
from .terminal import Progress, banner

HELP = (
    "S <text>         Search numbers or command words",
    "N                Next page",
    "P                Previous page",
    "JMP <page>       Go to a page",
    "B                Back to Search",
    "H                Show help",
    "Q                Quit",
    "--export         Export the result list",
    "--advexport      Export results with details",
    "",
    "Gift --export",
    "Gift --advexport",
    "S 123",
)


def show_help():
    for line in HELP:
        say(line)



def default_store():
    return Path.cwd() / ".bloxscope"


def prompt(text):
    from .terminal import finish_active
    finish_active()
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
                    say("Saved: " + str(path.resolve()), tone="success")
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
                for line in ("B  Back to results", "T  Show full string", "R  Check references", "C  Check pointer candidates", "V  Show raw evidence", "E  Export JSON and Markdown", "N/P  Browse reference pages", "Q  Quit"):
                    say(line)
        except (ScopeError, OSError, ValueError) as exc:
            d["reference_analysis"] = {"status": "unsupported-or-unresolved", "scope": str(exc)}
            say(str(exc))


def export_search(search, term, cached=False, *, advanced=False):
    progress = Progress()
    try:
        destination = destination_for(desktop_directory(), term, advanced=advanced)
        say("Preparing an exact-size TXT preview of all matching locations...", tone="heading")
        with preview(search, term, cached=cached, progress=progress, advanced=advanced) as prepared:
            progress.finish()
            say(f"Export all results for {term!r}?", tone="heading")
            say("Export type: " + ("Advanced" if advanced else "Basic"))
            say(f"Matches: {prepared.count:,}")
            say("TXT size: " + size(prepared.bytes))
            say("Save to: " + str(destination))
            while True:
                answer = prompt("Confirm export? [Y/N]: ").strip().upper()
                if answer == "Y":
                    publish(prepared, destination)
                    say("Saved: " + str(destination), tone="success")
                    return
                if answer in {"N", "Q", "B", ""}:
                    say("Export cancelled. No Desktop file was created.", tone="notice")
                    return
                say("Enter Y to save or N to cancel.", tone="notice")
    except KeyboardInterrupt:
        say("Export cancelled. Your search is still open.", tone="notice")
    except (ScopeError, OSError, ValueError, sqlite3.Error) as exc:
        say("Export failed: " + str(exc), tone="notice")
    finally:
        progress.finish()


def interactive(search, cached=False):
    summary = search.summary
    say(f"Search index: {summary['occurrences']:,} extracted string locations. Observed {summary['observed_at']}.", tone="heading")
    for o in summary["observations"]:
        say(f"  {o['product']}: {o['build']}")
    if summary.get("fixture_data"):
        say("SYNTHETIC TEST FIXTURE. These are not Roblox discoveries.", tone="notice")
    say("Enter a search term. H gives help. S <term> searches numbers or command words.")
    term, page, rows, total = None, 0, [], 0
    while True:
        line = prompt("Search: " if term is None else "Result number, new search, N/P, JMP <page>, --export/--advexport, B, H or Q: ")
        command = line.strip().upper()
        explicit = line[:2].upper() == "S "
        export_after = False
        if not explicit and command in {"--EXPORT", "--ADVEXPORT"}:
            if term is None:
                say("Enter a search term first.", tone="notice")
            else:
                export_search(search, term, cached, advanced=command == "--ADVEXPORT")
            continue
        if not explicit and re.match(r"^JMP(?:\s|$)", command):
            if term is None:
                say("Enter a search term first.", tone="notice")
                continue
            last_page = max(1, (total+14)//15)
            target = command[3:].strip()
            if not (target.isascii() and target.isdecimal() and len(target) <= 12 and 1 <= int(target) <= last_page):
                say(f"Use JMP <page>. Valid pages: 1-{last_page}. Your current page is {page+1}.", tone="notice")
                continue
            page = int(target)-1
            # Continue through the normal page rendering below.
            command = "N"
            jumped = True
        else:
            jumped = False
        if not explicit and command == "Q":
            return
        if not explicit and command == "H":
            show_help()
            continue
        if not explicit and command == "B":
            term, rows, page = None, [], 0
            continue
        if not explicit and command in {"N", "P"}:
            if term is None:
                say("Enter a search term first.")
                continue
            if jumped:
                pass
            elif command == "N" and (page+1)*15 < total:
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
            if not explicit:
                match = re.fullmatch(r"(.*\S)[ \t]+(--export|--advexport)[ \t]*", line, flags=re.IGNORECASE)
                if match:
                    new_term, export_after = match[1], match[2].lower()
            if not new_term:
                continue
            term, page = new_term, 0
        say("Searching...")
        total, rows = search.page(term, page)
        say()
        say(f"{total:,} matching extracted occurrences for {term!r}. Page {page+1}/{max(1, (total+14)//15)}. Duplicate text has separate locations.")
        for i, row in enumerate(rows, page*15+1):
            text = row["text"]
            preview = text if len(text) <= 150 else text[:150]+"..."
            say(f"{i}. {preview} [{row['type']}]")
        if export_after:
            export_search(search, term, cached, advanced=export_after == "--advexport")


def main(argv=None):
    parser = argparse.ArgumentParser(description="BloxScope: launch, prepare, and search public Roblox resource strings.")
    parser.add_argument("--store", type=Path, default=default_store(), help="Separate local evidence and index folder")
    parser.add_argument("--cached", action="store_true", help="Explicitly search the last completed index without a network check")
    parser.add_argument("--prepare-only", action="store_true", help="Prepare the default collection, then exit")
    parser.add_argument("--status", action="store_true", help="Print retained coverage and measured sizes")
    args = parser.parse_args(argv)
    root = args.store.resolve()
    banner()
    say("Data folder: " + str(root))
    try:
        if not args.cached and not args.status:
            try:
                prepare(root, Progress())
            except (ScopeError, OSError, ValueError) as exc:
                say("Preparation failed: " + str(exc), tone="notice")
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
                say("Preparation finished.", tone="success")
            else:
                if args.cached:
                    say("CACHED DATA. Build and observation dates are shown below. No fresh package observation is claimed.", tone="notice")
                interactive(search, cached=args.cached)
        finally:
            search.close()
        return 0
    except KeyboardInterrupt:
        say("Stopped. Completed packages and files can be reused. The last successful index stays available.")
        return 130
    except (ScopeError, OSError, ValueError, sqlite3.Error) as exc:
        say("BloxScope could not continue: " + str(exc))
        return 2
