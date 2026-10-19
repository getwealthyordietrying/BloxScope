# Feasibility and coverage

Measured October 19, 2026, America/New_York. Evidence timestamps are UTC. The real study below uses directly collected official Roblox bytes. Test fixtures are separate and labelled synthetic.

## Demonstrated result

BloxScope prepared a searchable index of **6,894,397 string locations** from **7,756 extracted file occurrences** and **79,080 script properties**. It runs independently of BloxRadar. Nothing downloaded from Roblox was executed.

The normal terminal workflow was verified against this index: automatic version/manifest check, verified cache reuse, literal search, paging, numbered details, back, numeric search, raw evidence, JSON/Markdown export, decoded references, pointer candidates and quit. The session exited with code 0 under strict ASCII output, without stderr. That refresh downloaded zero package bytes. See [the terminal record](real-terminal-verification.json).

`gift` returned **757 extracted occurrences**. `GiftAllowanceView` and `GiftEligibility` each returned 4. These are bounded string observations, not object counts, behavior recovery, feature announcements or release evidence. `123` returned 3,077. Literal `100%_[]` and `café` returned zero in this measured coverage; synthetic fixtures separately test punctuation and multibyte text. Zero matches do not prove a string is absent from Roblox.

| Extracted source type | Occurrences |
|---|---:|
| executable string | 2,817,821 |
| readable script Source | 21,320 |
| readable script file | 1,067,043 |
| readable text resource | 20,862 |
| script instance name | 79,080 |
| script string-table entry | 2,888,271 |

Player had 38,066 supported exact-prefix Luau tables and 22 readable Source properties. Studio had 40,879 raw Luau tables, 112 readable Source properties and one opaque Source property. This rechecks the old BloxRadar format observations using newly downloaded bytes.

The exact encoding, minimum length, coordinate rules, scope and bounds are in [README.md](../README.md). Byte extraction from executable sections is heuristic and can include printable noise in protected/opaque data. Search counts are not counts of meaningful source symbols.

## Verified direct sources

- [Player version](https://clientsettingscdn.roblox.com/v2/client-version/WindowsPlayer): `version-11f155f3450f4c8e`, observed version `0.743.0.7431134`.
- [Studio version](https://clientsettingscdn.roblox.com/v2/client-version/WindowsStudio64): `version-4f087c32742644ab`, observed version `0.743.0.7431133`.
- [Player manifest](https://setup.rbxcdn.com/version-11f155f3450f4c8e-rbxPkgManifest.txt) and [Studio manifest](https://setup.rbxcdn.com/version-4f087c32742644ab-rbxPkgManifest.txt): HTTP 200, v0 package records.
- Selected package URLs came from those manifest names under `https://setup.rbxcdn.com/{build}-{package}`. Each downloaded archive matched its manifest size and MD5, with an independent retained SHA-256.

There were 7 selected Player and 9 selected Studio package occurrences, covering 13 unique archives. Absent optional packages and all omitted distribution packages are recorded. There is no hidden-channel discovery, alternate tracker, authenticated source or full-distribution download. Endpoint availability was measured, not promised for future builds.

Raw version and manifest responses, HTTP metadata and selection records are retained in the local store. Package provenance separates original download URL/time from current requested URL and cache verification. Every result can open its source information with V. Discovery came from official bytes; the Luau repository and Microsoft/Capstone docs supplied only format information.

## Real reference evidence

Studio file: `RobloxStudio.zip/RobloxStudioBeta.exe`.

SHA-256: `cc70176d50d5f2c1edc26d40f1fcdc530748fc83dedc63c2fdbb251f766972d2`.

The retained UTF-8 string is `%s RobloxApp/%s (%s; RobloxDirectDownload)` at file offset `0x889b608`, RVA `0x889d008`, preferred VA `0x14889d008`.

One verified decoded reference is:

```text
Instruction VA: 0x14079c41f
File offset:    0x79b81f
Bytes:          488d15e20b1008
Decoded:        lea rdx, [rip + 0x8100be2]
Target VA:      0x14889d008
```

The exact bytes were checked against the retained original hash. The instruction boundary was independently re-decoded from its runtime range start. The signed displacement plus next-instruction VA equals the target, which maps back to the exact string bytes. See [the real instruction record](real-reference-investigation.json) and [independent checks](real-verification.json).

The pass visited 411,984 validated x64 unwind ranges and decoded 133,455,101 instruction bytes. It retained 685,164 supported mapped target references across the image. That number is not a count of string references or functions. 2,010 ranges stopped before their declared end. The reader does not resynchronize after failure. Forms outside RIP-relative LEA/MOV and mapped MOV immediates remain unresolved. Linear decoding is not a control-flow or reachability proof.

Installed Capstone package metadata is 5.0.9; its Python binding reports 5.0.7 and the native version tuple is `[5, 0, 1280]`. The evidence records these values without treating the binding label as the installed package version.

**Player blocker:** the collected executable has exception-directory RVA 0 and size 0. This backend cannot establish its required unwind instruction boundaries. It reports unsupported analysis instead of treating raw address patterns as code. Player strings are still searchable. No unpacking or bypass was attempted.

Controlled fixture tests independently demonstrate one LEA reference and one pointer/data candidate. A matching instruction pattern outside a fixture's unwind ranges is not accepted. Fixture results are never real Roblox discoveries. Packaged script tables and names are not call references; script instruction/call analysis is unsupported.

## Download and storage

| Measurement | Bytes |
|---|---:|
| Selected package sum, counting both products | 320,835,197 |
| Actual unique downloaded archives | 319,297,803 |
| Selected manifests' declared ZIP output | 538,874,746 |
| Extracted original file occurrence bytes | 464,798,391 |
| Unique retained original file blobs | 460,050,931 |
| Active search database | 2,171,133,952 |
| Current whole local data store | 5,272,638,216 |

The archive total is about 304.5 MiB. Current store use is about 4.91 GiB. It includes **two completed development generations**, retained to keep their evidence available. The same measured data with only the active generation would occupy 3,117,293,721 bytes (about 2.90 GiB). This is arithmetic for this dataset, not a future storage promise. No generations were silently pruned.

| Current disk component, including its metadata | Bytes |
|---|---:|
| packages | 319,308,649 |
| blobs | 460,050,931 |
| generations | 4,327,605,459 |
| analysis | 165,585,301 |
| observations | 64,890 |
| staging | 0 |
| exports | 22,671 |

Original packages and selected original file bytes remain available for retained results. The virtual environment, source release and verification transcripts are outside these store figures. Indexes, analysis, old builds and exports add storage; no fixed total limit is claimed. Individual transfer/decompression bounds are enforced as documented in the README.

## Verification and remaining limits

The 29 isolated regression tests passed. They cover first launch, automatic selection/preparation, cache reuse, changed-build package/extraction reuse, failed downloads, partial extraction, clean interruption, hard exit/restart, competing processes, crash-released locks, search paging/details/back, numeric terms, case folding, literal punctuation, Unicode, duplicates, exact UTF-8/UTF-16/prefixed/chunk coordinates, reference classifications, strict ASCII, cached fallback and synthetic provenance.

A source-only ZIP was extracted into a new temporary folder. Its tests and a separate full terminal session passed with strict ASCII output, including R/C reference evidence and exports. The verifier rejects downloaded-resource/database/cache entries. The final ZIP hash and clean verification record are delivered beside the release in `dist` and `verification`.

Real raw-evidence checks verified exact retained bytes for executable text, readable script files, readable Source, string-table entries and instance names. Synthetic tests also verify Zstandard property coordinates have no false file-offset attribution. Earlier archive preparation rejected Roblox's root-style directory markers; the reader now ignores directory entries and validates every file path. Failed attempts stayed unpromoted and their verified completed work was reused.

Known real coverage gaps are the Player and Studio XML `R6.rbxmx` files, loose Studio `CurveUtil.lua` with unsupported control bytes, and the corresponding opaque Source property in Studio `ServerCoreScripts.rbxm`. Original bytes are retained. They are not described as decoded scripts.

The default scope excludes built-in plugins, libraries and much other distribution content. Executable strings have heuristic boundaries. Unknown property formats stay opaque; UTF-16 executable strings need the documented ASCII anchor. Searches use full SQLite substring scans and can take seconds on this multi-million-row index. Pages hold only 15 result rows in Python. There is no hidden result cutoff. Reference recovery is partial and experimental; Player reference analysis and script call analysis remain unsupported. No execution, feature availability, eligibility, object identity, release date or release probability follows from a name or static reference.
