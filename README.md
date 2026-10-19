# BloxScope

Search strings in a local selection of public Roblox Windows Player and Studio resources. The app downloads them directly from Roblox. It does not need an account, cookies, an API key, a browser session, or an installed Roblox client. Downloaded code is never run.

Use Python 3.14 or newer. Zstandard is part of its standard library. Core search has no pip dependencies.

From the folder containing this README:

```powershell
py -3.14 -m bloxscope
```

The app checks official versions and prepares the index before asking for a term. It shows the selected download size and progress. The first preparation can take several minutes and several GB of disk space. Later launches verify and reuse cached data. Data stays in `.bloxscope` in the current folder. Keep launching from this folder, or use `--store "C:\path\to\your\store"`.

Optional executable analysis uses [Capstone 5.0.9](https://pypi.org/project/capstone/5.0.9/). Install it in a virtual environment:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install capstone==5.0.9
.\.venv\Scripts\python.exe -m bloxscope
```

This does not need PowerShell activation. Optional full installation is `py -3.14 -m pip install ".[analysis]"`, then `bloxscope`. Packaging may download setuptools. Module launch needs no installation.

## First search

1. Launch the app and wait for `Search:`.
2. Enter a term, for example `gift`. This is an example query, not a promised finding.
3. Enter a displayed result number to read it. `N` and `P` change pages.
4. In details, use `B` to return. Enter another term to search again, or `Q` to quit.

Use `S 123` to search a number. A plain number always means a displayed result number. `S Q` searches the letter Q. `S ` also preserves spaces and punctuation in the rest of the term. `H` explains the commands. Ctrl+C stops cleanly. EOF quits.

Search is a **case-insensitive literal substring**, using Unicode case folding. There is no regex, wildcard or SQL search mode. `%`, `_`, brackets and quotes are literal. Numbering continues across pages. Only numbers on the current page open details. Every matching extracted location remains available; there is no relevance cutoff. Counts are matching extracted string records, not unique text or individual repetitions of the term inside one record.

Details show the exact text, source type, build, original hash, encoding, length and location. Long text has a preview; `T` prints it all. Control characters and characters unsupported by the terminal are escaped. `V` shows raw evidence and URLs. `E` writes JSON and Markdown to the store's `exports` folder. Each export gets a new name.

For executable results, `R` checks experimental decoded references and `C` checks candidate pointer bytes. `N`/`P` then page through that evidence. Exports include the reference page currently open, its total count and coverage. Other pages remain accessible. Nearby context is only extraction order in the same file or script. It is not a reference.

## Collection and coverage

The default scope is fixed in `bloxscope/sources.py`. It uses the public LIVE `WindowsPlayer` and `WindowsStudio64` version responses, then their package manifests. No history feed, unofficial tracker, hidden channel, or restricted endpoint is used. Public availability is checked by real HTTP requests and package verification. These observed distribution interfaces can change.

The executable packages are `RobloxApp.zip` and `RobloxStudio.zip`. Only their exact main executables are extracted. Other selected packages, when present in the manifest, are:

```text
ApplicationConfig.zip        content-configs.zip
content-models.zip           extracontent-scripts.zip
extracontent-luapackages.zip  extracontent-models.zip
extracontent-places.zip      extracontent-translations.zip
studiocontent-models.zip
```

The app reports absent packages. It does not replace them with guesses or expand to the full distribution. Built-in plugins, libraries, fonts, textures, installers and most other packages are outside this scope. Archive inventories record selected and skipped members.

Supported extracted strings:

| Source | Encoding and minimum | Location |
|---|---|---|
| Main executable, sections without the executable flag | UTF-8 printable runs, at least 4 characters | Original file byte offset, section, RVA, preferred VA |
| Same executable sections | UTF-16LE printable BMP runs with at least 4 consecutive ASCII code units; no surrogate pairs | Original file byte offset, section, RVA, preferred VA |
| Loose `.lua`, `.luau`, `.json`, `.xml`, `.txt`, `.csv`, `.toml`, `.rbxp` files | UTF-8, or BOM-marked UTF-16LE/BE; nonempty lines, at least 1 character | Original file byte offset and line |
| Observed binary v0 model/place containers | Name properties, readable UTF-8 type-1 Source lines, type-29 supported Luau string tables; at least 1 character | Property chunk, flat class/instance slot, Source/payload byte coordinates |

Executable extraction is a byte-string heuristic. Protected or opaque sections can contain printable noise. Section flags do not prove a section contains meaningful text. Runs are not a recovery of C++ objects or source code. UTF-16BE and pure non-ASCII UTF-16 executable strings without the required ASCII anchor are outside coverage.

Container framing, LZ4 and Zstandard decoding are bounded. The Luau reader accepts header versions 3–9 and supported types versions 1–3. It reads only the leading string table. It also recognizes the exact observed eight-byte prefix `527a104a5b3209e0`. Its meaning and integrity are unknown. Unknown headers are not stripped. Non-UTF-8/empty string-table entries are not indexed. The rest of the bytecode is not validated, executed or decompiled. XML model serialization and other Source encodings remain unsupported.

Name attribution is flat and local to the file. Duplicate names are marked ambiguous. A class ID, instance slot, or name is not a permanent identity across builds. Opaque Source properties still have a name when supported attribution is available; that does not make their script content searchable.

These are **all extracted matches within this coverage**. They are not every related string anywhere in Roblox. Text duplicates retain separate file/script locations. Identical text does not show that two objects or features are related.

## References and evidence

Reference support is experimental, even where real instruction evidence was demonstrated. It supports AMD64 PE32+ images with validated x64 runtime-function/unwind ranges. Capstone linearly decodes from each range start and stops at an invalid instruction. It never scans arbitrary byte alignments for code or skips invalid bytes to resynchronize.

Supported reference forms are RIP-relative memory operands in LEA/MOV and mapped immediate VAs in MOV. Targets may be inside the recorded string. Each reference keeps instruction VA, file offset, exact bytes, decoded text, target VA and its validity explanation. The address is a preferred-image VA, not a live process address. RVAs and file offsets are different coordinates.

Unwind bounds and successful decoding support a static instruction interpretation. This is not a proof of reachability or execution. There is no symbol-based function naming, control-flow recovery, dynamic analysis, indirect pointer following, exhaustive reference recovery, or packaged-script call-reference analysis. Exact 64-bit VA bytes in non-executable sections are **candidate pointer/data references**, not decoded code. Context strings are not references.

The measured Studio build has demonstrated decoded instruction references. The measured Player executable has an exception directory RVA and size of zero, so this backend cannot establish its required instruction boundaries. Player references are unsupported by this reader. Player string search still works. See [the feasibility report](reports/FEASIBILITY.md) and the labelled JSON evidence there.

A name or reference does not prove a feature is enabled, reachable, new, announced, available to users, or planned for release. It does not give release dates or probabilities. For example, a name containing `GiftAllowance` does not establish a Robux spending limit.

## Refresh and recovery

Every normal launch checks both official version responses and manifests. Different observed builds create a new generation. Verified packages are reused by checksum and SHA-256; unchanged file extraction can also be reused within the same analysis version. The current requested URL stays separate from the original download URL/time. A cache hit is not a new response from the package URL.

Packages must match the manifest's byte count and MD5. SHA-256 records identify retained evidence. This is not Authenticode/signature verification. Completed packages and files are checkpoints. Partial HTTP bodies restart; interrupted files are parsed again. SQLite transactions roll back unfinished file indexing. An OS lock prevents competing preparations. Readers use the last completed generation.

A completed database and coverage record are promoted before the active pointer changes atomically. A failed refresh preserves the previous index and original bytes. The app reports the error and offers `C` to search the cache, with its builds and observation date visible. `--cached` explicitly skips the network check. `--prepare-only` prepares and exits. `--status` shows detailed coverage and current storage bytes.

Bounds: 512 MiB per download, 1 GiB of selected package bytes across products, 2 GiB of declared selected ZIP output, 768 MiB/60,000 entries per ZIP, 384 MiB per extracted member, 128 MiB per container chunk and 256 MiB of declared chunk output per container. Crossing a bound fails preparation; it does not silently widen scope. The bounds are safety checks, **not a fixed storage limit**. Databases, retained generations, observations, indexes, analysis and exports add storage. There is no automatic pruning. Keep originals needed by retained results.

## Tests and source release

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
.\.venv\Scripts\python.exe tools\package_source.py
.\.venv\Scripts\python.exe tools\verify_release.py
```

Tests use isolated temporary stores and clearly labelled synthetic resources. The real study uses only directly downloaded official bytes. Tests cover collection, cache/change handling, failed refresh, partial extraction, interruption/restart, locks, terminal navigation, numeric and literal searches, offsets, reference classes and strict ASCII output.

Without Capstone, the two backend-specific tests are skipped. The complete release check needs the optional analysis dependency. It also runs core search with site packages disabled to check the dependency-free path.

The source ZIP includes code, tests, docs, small evidence reports and required notices. It excludes downloaded Roblox resources, databases, caches, exports, virtual environments and verification stores. A clean-folder verification runs the packaged tests and terminal workflow. Read [the feasibility report](reports/FEASIBILITY.md) for measurements and remaining limits.

The container framing, bounded compression and Luau table reader were adapted from BloxRadar. Its MIT notice is preserved in [licenses/BloxRadar-MIT.txt](licenses/BloxRadar-MIT.txt).This project runs independently of BloxRadar.
