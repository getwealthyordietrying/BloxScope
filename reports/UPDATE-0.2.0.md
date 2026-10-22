# BloxScope 0.2.0 terminal update

This report describes the first unpublished draft. See [the refinement notes](REFINEMENT-0.2.0.md) for current colors, help and export commands.

This update adds full-search TXT export, direct page jumps, purple styling and preparation bars. Collection scope, encodings, offsets and experimental reference-analysis support are unchanged. Existing completed indexes are reused without a migration.

## Commands

- `Gift --export` searches and opens a confirmation for every matching location across all pages.
- `--export` offers the same export for the current search.
- `JMP <page>` opens any valid page. `JMP 32` is an example, not a fixed destination.
- `S <term>` is always literal: `S Gift --export` searches those characters, and `S JMP 32` searches that text. Numeric searches still work with `S 123`.

The TXT has a coverage/build/date header and full detailed records. Each record keeps its exact escaped string, file hash, source type, encoding, lengths, supported script attribution and separately labelled coordinates. Duplicate locations remain separate. This export does not run reference analysis.

An anonymous temporary UTF-8 preview provides the exact final byte size before confirmation. Desktop staging starts only after Y. The completed file is published without overwriting any existing path. N and Ctrl+C leave the search open. Temporary disk space is needed for a large preview and the confirmed copy. A hard process kill can leave a `.BloxScope-*.part` Desktop staging file; ordinary cancellation and copy errors are cleaned up.

Windows resolves the real Desktop through Known Folders, with a User Shell Folders fallback. OneDrive redirection was resolved read only on the development machine. All export write tests used temporary Desktop folders. Desktop permissions can still block saving; this is reported without silently choosing another folder.

## Appearance

The cat art from the update request appears once above preparation. Purple is used for headings and art, green for success and yellow for notices. Color is optional and disabled for redirection, NO_COLOR, dumb terminals and unsupported Windows consoles. Result data is escaped before any styling, and TXT output has no terminal control sequences.

Known transfer bytes, verification files/bytes and archive member counts get measured bars. Skipped archive members count as inspected decisions, not decoded scripts. Individual long script properties retain their inspected-count messages while their member is incomplete. Unknown totals show a stage/count without a percentage. Cache verification is labelled separately from downloads. The old checkpoint and promotion rules remain in place.

## Checks

The release checks cover 51 tests: the 29 existing regression tests and 22 new terminal/export tests. The new checks include both export actions, all-page output, full long strings, duplicate locations, exact saved size, Unicode/control escaping, confirmation/denial, Ctrl+C during preparation and copy, disk errors, racing filename collisions, redirected Desktop resolution, consistent generation selection after a refresh, literal command text, arbitrary/invalid page jumps, colors, measured progress, ASCII output and a subprocess terminal workflow.

The clean source verification extracts the release ZIP into a new temporary folder, runs the tests, exercises existing reference/detail exports and the new TXT/JMP workflow, and checks core search without site packages. It also checks license/resource exclusions and absence of the local home path. Its exact ZIP hash and results are delivered beside the source ZIP in `dist` and under `verification`.

A separate real-data CLI session used the retained index observed on October 22, 2026 (UTC timestamp `2026-10-22T15:24:15.337142+00:00`). Builds were Player `version-9ac8595712b447a7` and Studio `version-7aaa6838d36c4448`. `Gift` matched **1,469 extracted locations across 98 pages**. The session jumped to page 32, rejected an invalid page, denied the first export, then confirmed a current-search export. The resulting TXT contained all 1,469 records and exactly **2,530,801 bytes**, matching its confirmation. It ran under strict ASCII output with exit code 0 and no stderr. No packages were downloaded. The TXT was saved only to an isolated temporary Desktop and removed after verification. These real counts are not synthetic fixture results or feature claims.

## Source release

The source ZIP excludes Roblox resources, stores, databases, indexes, exports, virtual environments, build output and verification transcripts. It also omits the optional local code-reuse notes and research-input path list. Report copies redact machine-local home paths while keeping original hashes, offsets, build IDs, URLs and factual provenance. Historical local reports are unchanged. Required BloxRadar attribution comments, the README attribution paragraph and the MIT notice are retained.

The update does not add new decoding, decompilation, script call analysis or Player executable reference support. Existing coverage gaps and reference-analysis limits still apply. See [README.md](../README.md) for commands and [the original feasibility study](FEASIBILITY.md) for the earlier collection/reference evidence and storage measurements.
