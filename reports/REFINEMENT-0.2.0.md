# Unpublished 0.2.0 refinement

This refinement replaces the earlier purple display and detailed-only TXT action. The earlier UPDATE report describes the first draft of 0.2.0; use this note and the README for current commands.

Headings and art are cyan. Success stays green and notices stay yellow. Help is a short list of commands followed by three examples. Technical explanations stay in the README and details.

`--export` writes only a numbered list of full matching strings. `--advexport` writes the previous detailed records. Either flag can follow a new search or act on the current search. Both formats preserve every location across all pages, display order and duplicates. `S <text>` bypasses both actions and page commands. The confirmation identifies the export format and exact UTF-8 size. Filenames distinguish basic and advanced files. Desktop resolution, confirmation, streaming, cleanup and no-overwrite promotion are unchanged.

Progress redraws on one line in an interactive terminal. Known totals show a solid bar, counts and percentage. Unknown totals show activity without a percentage. Bar width follows the terminal width. Unicode-capable output uses blocks; ASCII terminals use hash marks. Redirected output uses occasional plain lines. NO_COLOR disables color but does not disable measured progress. Messages and prompts finish any active progress line first.

The regression suite now has 55 tests. Added checks cover the exact basic format, full text and ordering, both export actions, literal flags, filename distinction, short help, cyan styling, same-line redraw, terminal width and Unicode/ASCII fallback. Existing collection, cache, failure, cancellation, reference and offset tests remain in the suite. All stores and Desktop write tests use temporary folders.

The replacement source ZIP is verified from a fresh extracted folder, including both TXT workflows and core operation without site packages. Exact results and the final ZIP hash are recorded beside the release. The source-only exclusions and required licenses remain in place. Version stays 0.2.0 because it has not been published.

Collection scope and decoding/reference limits are unchanged. Large exports still need temporary disk space. Terminal palettes determine the exact cyan shade. Very narrow terminals may shorten stage labels and counts to avoid wrapping. No downloaded Roblox code is executed and nothing is published by these checks.
