"""SYNTHETIC terminal/export checks. Desktop writes use temporary folders."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bloxscope.cli import interactive
from bloxscope.common import ScopeError, say
from bloxscope.export_txt import desktop_directory, destination_for, preview, publish, _registry_desktop
from bloxscope.sources import HTTP
from bloxscope.store import Search
from bloxscope.terminal import ART, Progress, banner, progress_text, supports_color
from tests.fixtures import FakeHTTP, model_fixture
from tests.test_core import TempCase


class ExportTests(TempCase):
    def test_basic_format_full_order_duplicates_and_no_metadata(self):
        rows = [row["text"] for row in self.search.export_rows("gift")]
        from bloxscope.common import safe
        with preview(self.search, "gift") as prepared:
            data = prepared.stream.read()
            expected = "".join(f"{i}. {safe(text)}\n" for i, text in enumerate(rows, 1)).encode("utf-8")
            self.assertEqual(data, expected)
            self.assertEqual(prepared.bytes, len(data))
            self.assertNotIn(b"Original file SHA-256:", data)
        self.assertIn("-basic-", destination_for(self.desktop, "gift").name)
        self.assertIn("-advanced-", destination_for(self.desktop, "gift", advanced=True).name)

    def test_basic_and_advanced_actions_and_literal_flags(self):
        out = self.session(["Gift --export", "Y", "--advexport", "Y", "Gift --advexport", "N", "--export", "N", "S Gift --advexport", "S --advexport", "Q"])
        self.assertEqual(out.count("Export type: Basic"), 2)
        self.assertEqual(out.count("Export type: Advanced"), 2)
        self.assertIn("for 'Gift --advexport'", out)
        basic, = self.desktop.glob("*-basic-*.txt")
        advanced, = self.desktop.glob("*-advanced-*.txt")
        self.assertTrue(basic.read_text(encoding="utf-8").startswith("1. "))
        self.assertIn("Original file SHA-256:", advanced.read_text(encoding="utf-8"))
    def setUp(self):
        super().setUp()
        self.long_text = 'Gift SYNTHETIC ' + 'café 漢字' * 900 + '\n\t\x1b[31m\u202e literal \\n'
        self.collect(FakeHTTP(model=model_fixture(strings=["SYNTHETIC FIXTURE", self.long_text, "GiftFixture", "GiftFixture", "Gift --export", "JMP 32"])))
        self.search = Search(self.root)
        self.desktop = self.root/"OneDrive"/"Desktop"
        self.desktop.mkdir(parents=True)

    def tearDown(self):
        self.search.close()
        super().tearDown()

    def session(self, commands):
        output = io.StringIO()
        with patch("bloxscope.cli.desktop_directory", return_value=self.desktop), patch("builtins.input", side_effect=commands), redirect_stdout(output):
            interactive(self.search, cached=True)
        return output.getvalue()

    def test_all_pages_full_strings_duplicate_locations_exact_utf8_size(self):
        count = self.search.count("gift")
        self.assertGreater(count, 30)
        with patch("bloxscope.pe.build_references", side_effect=AssertionError("No analysis during export")):
            with preview(self.search, "gift", advanced=True, cached=True) as prepared:
                self.assertEqual(list(self.desktop.iterdir()), [])
                data = prepared.stream.read()
                self.assertEqual(prepared.bytes, len(data))
                text = data.decode("utf-8")
                self.assertEqual(len(re.findall(r"^Result \d+ \| occurrence", text, re.MULTILINE)), count)
                ids = re.findall(r"^Result \d+ \| occurrence (\d+)", text, re.MULTILINE)
                self.assertEqual(len(ids), len(set(ids)))
                self.assertGreater(text.count('Exact string: "GiftFixture"'), 2)
                self.assertIn('café 漢字' * 900, text)
                self.assertIn('\\n\\t\\u001b[31m', text)
                self.assertNotIn('\x1b', text)
                for field in ("Index generation:", "Cached mode (no successful refresh this session): Yes", "Origin: synthetic-test-fixture", "Original file SHA-256:", "Source-relative", "payload offset:", "Name attribution:"):
                    self.assertIn(field, text)
                destination = destination_for(self.desktop, "gift")
                publish(prepared, destination)
                self.assertEqual(destination.read_bytes(), data)
        self.assertTrue(prepared.stream.closed)

    def test_both_export_commands_confirm_current_term_and_real_size(self):
        out = self.session(["Gift --advexport", "N", "--advexport", "Y", "Q"])
        files = list(self.desktop.glob("*.txt"))
        self.assertEqual(len(files), 1)
        self.assertEqual(out.count("Export all results for 'Gift'?"), 2)
        displayed_sizes = re.findall(r"TXT size: ([\d,]+) bytes", out)
        self.assertEqual(int(displayed_sizes[-1].replace(",", "")), files[0].stat().st_size)
        self.assertIn("Export cancelled", out)
        self.assertIn("Saved:", out)
        self.assertIn('Search term: "Gift"', files[0].read_text(encoding="utf-8"))

    def test_denial_creates_no_desktop_file_and_keeps_page(self):
        out = self.session(["gift", "JMP 2", "--export", "N", "16", "B", "Q"])
        self.assertFalse(list(self.desktop.iterdir()))
        self.assertIn("Found:", out)
        self.assertIn("Page 2/", out)

    def test_no_current_search_and_invalid_confirmation(self):
        out = self.session(["--export", "Gift --export", "maybe", "N", "Q"])
        self.assertIn("Enter a search term first.", out)
        self.assertIn("Enter Y to save or N to cancel", out)
        self.assertFalse(list(self.desktop.iterdir()))

    def test_cancel_confirmation_with_ctrl_c_and_continue(self):
        out = self.session(["Gift --export", KeyboardInterrupt(), "JMP 2", "Q"])
        self.assertIn("Export cancelled. Your search is still open.", out)
        self.assertIn("Page 2/", out)
        self.assertFalse(list(self.desktop.iterdir()))

    def test_interrupted_preview_closes_temporary_file(self):
        real_rows = self.search.export_rows
        def interrupted(term):
            for number, row in enumerate(real_rows(term)):
                if number == 3:
                    raise KeyboardInterrupt
                yield row
        with patch.object(self.search, "export_rows", interrupted):
            out = self.session(["Gift --export", "Q"])
        self.assertIn("Export cancelled", out)
        self.assertFalse(list(self.desktop.iterdir()))
        self.assertGreater(self.search.count("gift"), 30)

    def test_interrupted_copy_cleans_partial_and_preserves_index(self):
        original_generation = self.search.generation
        with preview(self.search, "gift", advanced=True) as prepared:
            with patch("bloxscope.export_txt.shutil.copyfileobj", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
                publish(prepared, destination_for(self.desktop, "gift"))
        self.assertFalse(list(self.desktop.iterdir()))
        self.assertEqual(self.search.generation, original_generation)
        self.assertGreater(self.search.count("gift"), 30)

    def test_disk_error_is_clear_and_search_stays_open(self):
        with patch("bloxscope.cli.publish", side_effect=OSError("SYNTHETIC disk full")):
            out = self.session(["Gift --export", "Y", "JMP 2", "Q"])
        self.assertIn("Export failed: SYNTHETIC disk full", out)
        self.assertIn("Page 2/", out)

    def test_racing_filename_collision_never_overwrites(self):
        destination = destination_for(self.desktop, "gift")
        destination.write_bytes(b"SYNTHETIC existing file")
        with preview(self.search, "gift", advanced=True) as prepared:
            with self.assertRaises(FileExistsError):
                publish(prepared, destination)
        self.assertEqual(destination.read_bytes(), b"SYNTHETIC existing file")
        self.assertEqual(len(list(self.desktop.iterdir())), 1)

    def test_empty_export_and_literal_punctuation(self):
        with preview(self.search, "NO_SYNTHETIC_MATCH", advanced=True) as prepared:
            text = prepared.stream.read().decode()
            self.assertEqual(prepared.count, 0)
            self.assertNotIn("Result 1 |", text)
        with preview(self.search, "100%_[]", advanced=True) as prepared:
            self.assertEqual(prepared.count, self.search.count("100%_[]"))
            self.assertGreater(prepared.count, 30)

    def test_export_keeps_its_generation_after_another_refresh(self):
        generation = self.search.generation.name
        self.collect(FakeHTTP(revision=2))
        from bloxscope.store import active
        self.assertNotEqual(active(self.root).name, generation)
        with preview(self.search, "revision", advanced=True) as prepared:
            text = prepared.stream.read().decode("utf-8")
            self.assertIn("Index generation: " + generation, text)
            self.assertIn('Exact string: "revision 1"', text)
            self.assertNotIn('Exact string: "revision 2"', text)

    def test_literal_commands_never_export_or_jump(self):
        out = self.session(["S Gift --export", "S JMP 32", "S --export", "S 123", "Q"])
        for text in ("for 'Gift --export'", "for 'JMP 32'", "for '--export'", "for '123'"):
            self.assertIn(text, out)
        self.assertNotIn("Confirm export", out)
        self.assertFalse(list(self.desktop.iterdir()))

    def test_any_valid_page_and_invalid_jumps_preserve_page(self):
        last = (self.search.count("gift")+14)//15
        commands = ["JMP 2", "gift", f"JMP {last}", "JMP 2", "JMP 0", "JMP -1", "JMP nope", "JMP", "JMP 99999999999999999999", "JMP 999", "16", "B", "JMP 1", "Q"]
        out = self.session(commands)
        self.assertIn(f"Page {last}/{last}", out)
        self.assertEqual(out.count("Valid pages: 1-"), 6)
        self.assertIn("Found:", out)
        self.assertIn("Page 1/", out)


class DesktopTests(TempCase):
    def test_known_folder_can_be_redirected_to_onedrive(self):
        desktop = self.root/"OneDrive"/"Desktop"
        desktop.mkdir(parents=True)
        with patch("bloxscope.export_txt.sys.platform", "win32"), patch("bloxscope.export_txt._known_desktop", return_value=desktop), patch("bloxscope.export_txt._registry_desktop", side_effect=AssertionError("No fallback needed")):
            self.assertEqual(desktop_directory(), desktop.resolve())

    def test_registry_fallback_expands_onedrive(self):
        desktop = self.root/"Desktop"
        desktop.mkdir()
        with patch("bloxscope.export_txt.sys.platform", "win32"), patch("bloxscope.export_txt._known_desktop", side_effect=OSError), patch("bloxscope.export_txt._registry_desktop", return_value=desktop):
            self.assertEqual(desktop_directory(), desktop.resolve())
        key = unittest.mock.MagicMock()
        registry = SimpleNamespace(HKEY_CURRENT_USER=1, OpenKey=lambda *args: key,
                                   QueryValueEx=lambda *args: (r"%OneDrive%\Desktop", 2))
        with patch.dict(sys.modules, {"winreg": registry}), patch.dict(os.environ, {"OneDrive": str(self.root/"OneDrive")}):
            self.assertEqual(str(_registry_desktop()), str(self.root/"OneDrive"/"Desktop"))

    def test_unavailable_desktop_reports_error_without_guessing(self):
        with patch("bloxscope.export_txt.sys.platform", "win32"), patch("bloxscope.export_txt._known_desktop", side_effect=OSError), patch("bloxscope.export_txt._registry_desktop", side_effect=OSError), self.assertRaises(ScopeError):
            desktop_directory()

    def test_safe_unique_filename_handles_long_and_unicode_terms(self):
        a = destination_for(self.root, '../../CON:<bad> "gift" / 漢字' * 100)
        a.write_text("existing")
        b = destination_for(self.root, '../../CON:<bad> "gift" / 漢字' * 100)
        self.assertNotEqual(a, b)
        self.assertEqual(a.parent, self.root)
        self.assertLess(len(a.name), 140)
        self.assertNotRegex(a.name, r'[<>:"/\\|?*]')


class AppearanceTests(TempCase):
    def test_progress_redraw_width_unicode_ascii_and_prompt_boundary(self):
        class TTY(io.StringIO):
            encoding = "utf-8"
            def isatty(self): return True
        stream = TTY()
        with redirect_stdout(stream), patch.dict(os.environ, {"NO_COLOR": "1", "TERM": "xterm"}), patch("bloxscope.terminal.shutil.get_terminal_size", return_value=os.terminal_size((65, 24))), patch("bloxscope.terminal.time.monotonic", side_effect=[0, 1, 2]):
            progress = Progress()
            progress.update("Downloading resource", 0, 100, "bytes")
            progress.update("Downloading resource", 50, 100, "bytes")
            say("Search:")
        text = stream.getvalue()
        self.assertEqual(text.count("\r"), 2)
        self.assertIn("█", text)
        self.assertIn("\nSearch:\n", text)
        for line in text.split("\r")[1:]:
            self.assertLessEqual(len(line.split("\n")[0]), 64)
        class ASCII(TTY): encoding = "ascii"
        with redirect_stdout(ASCII()), patch.dict(os.environ, {"NO_COLOR": "1", "TERM": "xterm"}):
            p = Progress();p.update("Verifying", 5, 10);p.finish()
            self.assertNotIn("█", sys.stdout.getvalue())

    def test_short_help_has_one_command_per_line(self):
        from bloxscope.cli import show_help
        out = io.StringIO()
        with redirect_stdout(out): show_help()
        text = out.getvalue()
        self.assertNotIn(";", text)
        self.assertNotIn("No relevance cutoff", text)
        self.assertIn("--advexport      Export results with details\n", text)
    def test_cyan_success_and_notice_and_no_color(self):
        class TTY(io.StringIO):
            def isatty(self):
                return True
        stream = TTY()
        with patch("bloxscope.terminal.sys.platform", "linux"), patch.dict(os.environ, {}, clear=True), redirect_stdout(stream):
            say("heading", tone="heading")
            say("saved", tone="success")
            say("notice", tone="notice")
            say("\x1b[31m evidence")
        text = stream.getvalue()
        for code in ("36", "32", "33"):
            self.assertIn(f"\x1b[{code}m", text)
        self.assertIn("\\x1b[31m evidence", text)
        with patch.dict(os.environ, {"NO_COLOR": ""}), redirect_stdout(stream):
            self.assertFalse(supports_color())
        with redirect_stdout(io.StringIO()):
            self.assertFalse(supports_color())
        with patch.dict(os.environ, {"TERM": "dumb"}), redirect_stdout(stream):
            self.assertFalse(supports_color())

    def test_unsupported_windows_console_disables_color(self):
        with patch("bloxscope.terminal.sys.platform", "win32"), patch("ctypes.WinDLL") as dll, patch.dict(os.environ, {}, clear=True):
            dll.return_value.GetConsoleMode.return_value = 0
            stream = unittest.mock.Mock()
            stream.isatty.return_value = True
            self.assertFalse(supports_color(stream))

    def test_art_once_and_measured_bar_without_fake_unknown_percent(self):
        output = io.StringIO()
        with redirect_stdout(output):
            banner()
        self.assertEqual(output.getvalue().splitlines()[:-1], ART.splitlines())
        self.assertIn(" 50%", progress_text("Indexing", 5, 10, "members"))
        self.assertNotIn("%", progress_text("Indexing", 5, None))
        self.assertNotIn("100%", progress_text("Indexing", 0, 0))

    def test_known_unknown_download_progress_and_cache_verification(self):
        class Response(io.BytesIO):
            status = 200
            url = "https://setup.rbxcdn.com/synthetic-fixture"
        http = HTTP()
        for headers in ({"Content-Length": "4"}, {}):
            def opened(*args, **kwargs):
                r = Response(b"test")
                r.headers = headers
                return r
            http.opener = SimpleNamespace(open=opened)
            output = io.StringIO()
            with redirect_stdout(output):
                http.fetch(Response.url, self.root/"response", 4, Progress())
            self.assertEqual("100%" in output.getvalue(), bool(headers))
        self.collect()
        output = io.StringIO()
        from bloxscope.prepare import prepare
        with redirect_stdout(output):
            prepare(self.root, Progress(), FakeHTTP())
        text = output.getvalue()
        self.assertIn("Verifying retained files", text)
        self.assertIn("Verified cached package", text)
        self.assertIn("no download needed", text)

    def test_updated_terminal_workflow_strict_ascii_subprocess(self):
        code = "import sys;from pathlib import Path;from unittest.mock import patch;from tests.fixtures import FakeHTTP;from bloxscope.cli import main;\nwith patch('bloxscope.prepare.HTTP',FakeHTTP),patch('bloxscope.cli.desktop_directory',lambda:Path(sys.argv[1])): raise SystemExit(main(['--store',sys.argv[2]]))"
        commands = "Gift --advexport\nN\nJMP 3\nJMP 999\n31\nB\n--advexport\nY\nS Gift --export\nS 123\nH\nQ\n"
        env = {**os.environ, "PYTHONIOENCODING": "ascii:strict", "NO_COLOR": "1"}
        result = subprocess.run([sys.executable, "-c", code, str(self.root), str(self.root/"store")], input=commands.encode(), capture_output=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr.decode("ascii", "replace"))
        text = result.stdout.decode("ascii")
        for expected in ("Page 3/", "Valid pages:", "Found:", "Export cancelled", "TXT size:", "Saved:", "for 'Gift --export'", "for '123'"):
            self.assertIn(expected, text)
        self.assertEqual(text.count(ART.splitlines()[0]), 1)
        self.assertNotIn("\x1b", text)
        files = list(self.root.glob("*.txt"))
        self.assertEqual(len(files), 1)
        self.assertIn("Origin: synthetic-test-fixture", files[0].read_text(encoding="utf-8"))
