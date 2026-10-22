"""Regression checks use only temporary stores and synthetic bytes."""
from contextlib import redirect_stdout
import io
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from bloxscope.cli import export_detail, main
from bloxscope.common import ScopeError, digest, process_lock, say
from bloxscope.container import OBSERVED_PLAYER_PREFIX, script_strings, inspect_scripts, decompress
from bloxscope.extract import archive_members, executable_strings, extract_file, lines
from bloxscope.pe import PE, build_references, pointer_page
from bloxscope.prepare import prepare
from bloxscope.sources import HTTP, official, parse_manifest
from bloxscope.store import Search, active
from tests.fixtures import FakeHTTP, model_fixture, pe_fixture, table, zip_fixture


class TempCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def collect(self, http=None):
        return prepare(self.root, lambda _: None, http or FakeHTTP())


class CollectionTests(TempCase):
    def test_first_launch_and_cache_reuse(self):
        h = FakeHTTP()
        first = self.collect(h)
        self.assertIsNotNone(active(self.root))
        h.calls.clear()
        self.assertEqual(first, self.collect(h))
        self.assertEqual(len(h.calls), 4)
        self.assertEqual(json.loads((self.root/"progress.json").read_text())["stage"], "ready")

    def test_changed_build_reuses_same_packages(self):
        first = self.collect()
        h = FakeHTTP(2)
        second = self.collect(h)
        self.assertNotEqual(first, second)
        downloads = [x for x in h.calls if x.endswith(".zip")]
        self.assertEqual(len(downloads), 1)  # shared new script ZIP, both products
        self.assertTrue((first/"complete.json").exists())
        self.assertGreater(json.loads((second/"complete.json").read_text())["reused_file_extractions"], 0)

    def test_failed_refresh_keeps_searchable_old_generation(self):
        first = self.collect()
        with self.assertRaises(ScopeError):
            self.collect(FakeHTTP(2, "extracontent-scripts.zip"))
        self.assertEqual(active(self.root), first)
        s = Search(self.root)
        try:
            self.assertGreater(s.page("gift")[0], 30)
        finally:
            s.close()

    def test_interrupted_index_resume_without_duplicate_rows(self):
        from bloxscope.prepare import extract_file as real_extract
        calls = 0

        def interrupted(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt
            return real_extract(*args, **kwargs)

        with patch("bloxscope.prepare.extract_file", interrupted), self.assertRaises(KeyboardInterrupt):
            self.collect()
        self.assertIsNone(active(self.root))
        self.collect()
        s = Search(self.root)
        try:
            count = s.page("gift")[0]
        finally:
            s.close()
        other = self.root/"other"
        prepare(other, lambda _: None, FakeHTTP())
        s = Search(other)
        try:
            self.assertEqual(count, s.page("gift")[0])
        finally:
            s.close()

    def test_interrupted_download_restarts_partial_body(self):
        h = FakeHTTP()
        real_fetch = h.fetch
        once = True

        def interrupted(url, path, limit, progress=lambda _: None):
            nonlocal once
            if url.endswith(".zip") and once:
                once = False
                Path(path).write_bytes(b"SYNTHETIC PARTIAL")
                raise KeyboardInterrupt
            return real_fetch(url, path, limit, progress)

        h.fetch = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.collect(h)
        self.collect(h)
        self.assertIsNotNone(active(self.root))

    def test_partial_extraction_does_not_promote(self):
        first = self.collect()
        from bloxscope.prepare import extract_file as real_extract

        def broken(path, *args, **kwargs):
            if path.endswith(".lua"):
                return {"errors": ["SYNTHETIC truncated property"]}
            return real_extract(path, *args, **kwargs)

        with patch("bloxscope.prepare.extract_file", broken), self.assertRaises(ScopeError):
            self.collect(FakeHTTP(2))
        self.assertEqual(first, active(self.root))

    def test_competing_process_lock_and_crash_release(self):
        code = "from bloxscope.common import process_lock,ScopeError; import sys; "
        code += "\ntry:\n with process_lock(sys.argv[1]): print('LOCKED')\nexcept ScopeError: print('BUSY')"
        with process_lock(self.root):
            run = subprocess.run([sys.executable, "-c", code, str(self.root)], capture_output=True, text=True)
            self.assertEqual(run.stdout.strip(), "BUSY")
        run = subprocess.run([sys.executable, "-c", code, str(self.root)], capture_output=True, text=True)
        self.assertEqual(run.stdout.strip(), "LOCKED")
        crash = "import os,sys; from bloxscope.common import process_lock;\nwith process_lock(sys.argv[1]): os._exit(9)"
        run = subprocess.run([sys.executable, "-c", crash, str(self.root)], capture_output=True)
        self.assertEqual(run.returncode, 9)
        with process_lock(self.root):
            pass

    def test_fresh_terminal_launch_prepares_without_user_selection(self):
        code = "import sys; from unittest.mock import patch; from tests.fixtures import FakeHTTP; from bloxscope.cli import main; "
        code += "\nwith patch('bloxscope.prepare.HTTP', FakeHTTP): raise SystemExit(main(['--store',sys.argv[1]]))"
        run = subprocess.run([sys.executable, "-c", code, str(self.root)], input="gift\n1\nB\nQ\n", capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("Checking official Player and Studio versions", run.stdout)
        self.assertIn("SYNTHETIC TEST FIXTURE", run.stdout)
        self.assertIn("Found:", run.stdout)
        self.assertNotIn("Choose a product", run.stdout)

    def test_hard_exit_mid_index_recovers_sqlite_and_lock(self):
        code = "import os,sys; from unittest.mock import patch; from tests.fixtures import FakeHTTP; from bloxscope.prepare import prepare; "
        code += "\ndef crash(path,raw,emit,script,progress):\n emit('SYNTHETIC unfinished','test','utf-8',0,1,{},None)\n os._exit(19)\n"
        code += "\nwith patch('bloxscope.prepare.extract_file',crash): prepare(sys.argv[1],lambda _:None,FakeHTTP())"
        run = subprocess.run([sys.executable, "-c", code, str(self.root)], capture_output=True)
        self.assertEqual(run.returncode, 19, run.stderr)
        self.assertIsNone(active(self.root))
        self.collect()
        s = Search(self.root)
        try:
            self.assertEqual(s.page("unfinished")[0], 0)
            self.assertGreater(s.page("gift")[0], 30)
        finally:
            s.close()

    def test_cache_damage_is_reported(self):
        first = self.collect()
        next((self.root/"packages").glob("*.zip")).write_bytes(b"damaged")
        with self.assertRaises(ScopeError):
            self.collect()
        self.assertEqual(first, active(self.root))


class SearchTests(TempCase):
    def setUp(self):
        super().setUp()
        self.collect()
        self.s = Search(self.root)

    def tearDown(self):
        self.s.close()
        super().tearDown()

    def test_literal_case_unicode_duplicates_and_pagination(self):
        total, page = self.s.page("gift")
        self.assertEqual(total, self.s.page("GiFt")[0])
        self.assertGreater(total, len(page))
        ids, n = set(), 0
        while n*15 < total:
            _, rows = self.s.page("gift", n)
            ids.update(x["id"] for x in rows)
            n += 1
        self.assertEqual(len(ids), total)
        self.assertGreater(self.s.page("100%_[]")[0], 30)
        self.assertGreater(self.s.page("café")[0], 30)
        self.assertEqual(self.s.page("' OR 1=1 --")[0], 0)
        self.assertGreater(self.s.page("GiftFixture")[0], 2)

    def test_details_exact_bytes_and_synthetic_export(self):
        _, rows = self.s.page("café gift")
        for r in rows:
            d = self.s.detail(r["id"])
            self.assertEqual(d["origin"], "synthetic-test-fixture")
            offset = d["location"].get("file_offset")
            if offset is not None:
                data = (self.root/"blobs"/d["file"]["sha256"]).read_bytes()
                self.assertEqual(data[offset:offset+d["byte_length"]].decode(d["encoding"]), d["text"])
        files = export_detail(self.root, d)
        self.assertEqual(json.loads(files[0].read_text())["origin"], "synthetic-test-fixture")
        self.assertIn("synthetic-test-fixture", files[1].read_text())

    def test_actual_terminal_workflow_subprocess_strict_ascii(self):
        code = "import sys; from unittest.mock import patch; from tests.fixtures import FakeHTTP; from bloxscope.cli import main; "
        code += "\nwith patch('bloxscope.prepare.HTTP', FakeHTTP): raise SystemExit(main(['--store',sys.argv[1]]))"
        commands = "gift\nN\nP\n1\nB\nS 123\nB\nH\nS 100%_[]\n1\nE\nB\nQ\n"
        import os
        env = {**os.environ, "PYTHONIOENCODING": "ascii:strict"}
        run = subprocess.run([sys.executable, "-c", code, str(self.root)], input=commands.encode(), capture_output=True, env=env)
        self.assertEqual(run.returncode, 0, run.stderr.decode("ascii", "replace"))
        out = run.stdout.decode("ascii")
        for text in ("SYNTHETIC TEST FIXTURE", "Page 2/", "Found:", "Original file SHA-256", "for '123'", "Saved:", "Search numbers or command words"):
            self.assertIn(text, out)
        self.assertNotIn("Traceback", out)

    def test_failed_refresh_offers_labelled_cached_workflow(self):
        out = io.StringIO()
        with patch("bloxscope.prepare.HTTP", lambda: FakeHTTP(fail="client-version")), patch("builtins.input", side_effect=["C", "gift", "Q"]), redirect_stdout(out):
            code = main(["--store", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("CACHED DATA", out.getvalue())
        self.assertIn("version-0000000000000001", out.getvalue())

    def test_numeric_input_is_never_an_implicit_search(self):
        out = io.StringIO()
        with patch("builtins.input", side_effect=["123", "S 123", "Q"]), redirect_stdout(out):
            main(["--store", str(self.root), "--cached"])
        self.assertIn("For a numeric search", out.getvalue())
        self.assertIn("for '123'", out.getvalue())

    def test_clean_prompt_interruption(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt), redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--store", str(self.root), "--cached"]), 130)
        self.assertIsNotNone(active(self.root))


class ExtractionTests(TempCase):
    def test_multibyte_utf8_and_utf16_offsets(self):
        raw = pe_fixture()
        entries = list(executable_strings(raw))
        self.assertTrue(any(x[1] == "utf-16-le" and "漢字" in x[0] for x in entries))
        for text, encoding, offset, length, loc in entries:
            self.assertEqual(raw[offset:offset+length].decode(encoding), text)
            self.assertEqual(PE(raw).rva_to_file(loc["rva"]), offset)
        raw = "éé first\r\ngift 漢字".encode()
        found = list(lines(raw))
        self.assertEqual(found[1][1], len("éé first\r\n".encode()))

    def test_bom_text_offsets_in_both_utf16_byte_orders(self):
        for encoding, bom in (("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff"), ("utf-8", b"\xef\xbb\xbf")):
            raw = bom+"café\r\ngift 漢字".encode(encoding)
            records = []
            extract_file("fixture.lua", raw, lambda *r: records.append(r), lambda _: None)
            for text, kind, enc, off, length, loc, sid in records:
                self.assertEqual(raw[off:off+length].decode(enc), text)
            self.assertEqual(records[0][3], len(bom))

    def test_prefixed_source_payload_and_container_coordinates(self):
        raw = model_fixture()
        records, scripts = [], {}
        def script(e):
            n = len(scripts)+1
            scripts[n] = e
            return n
        coverage = extract_file("Fixture.rbxm", raw, lambda *x: records.append(x), script)
        self.assertFalse(coverage["errors"])
        for text, kind, enc, off, length, loc, sid in records:
            if loc.get("file_offset") is not None:
                self.assertEqual(raw[loc["file_offset"]:loc["file_offset"]+length].decode(enc), text)
            if kind == "script string-table entry":
                self.assertEqual(loc["source_offset"]-loc["payload_offset"], scripts[sid]["payload_offset"])
        self.assertTrue(any(e.get("payload_offset") == 8 for e in scripts.values()))
        self.assertEqual(scripts[1]["name_attribution"], "ambiguous-name")

    def test_unknown_prefix_and_opaque_source_are_not_decoded(self):
        with self.assertRaises(ScopeError):
            script_strings(b"12345678"+table(["gift"]))
        report = inspect_scripts(model_fixture(opaque=True))
        self.assertEqual(report["opaque_count"], 1)
        self.assertEqual(report["status"], "partial")

    def test_bad_varint_and_decompression_bounds(self):
        with self.assertRaises(ScopeError):
            script_strings(b"\x09\x03\x80\x00")
        with self.assertRaises(ScopeError):
            decompress(b"abc", 3, 129*1024**2)
        from compression import zstd
        raw = b"SYNTHETIC"*100
        packed = zstd.compress(raw)
        self.assertEqual(decompress(packed, len(packed), len(raw))[0], raw)
        with self.assertRaises(ScopeError):
            decompress(packed+b"trailing", len(packed)+8, len(raw))

    def test_zip_paths_sizes_and_corruption(self):
        for name in ("../bad.lua", "/bad.lua", "C:\\bad.lua", "a/../../bad.lua"):
            path = self.root/"bad.zip"
            path.write_bytes(zip_fixture({name: b"SYNTHETIC"}))
            with self.assertRaises(ScopeError):
                list(archive_members(path))
        path.write_bytes(zip_fixture({"a.lua": b"fixture", "A.lua": b"fixture"}))
        with self.assertRaises(ScopeError):
            list(archive_members(path))
        path.write_bytes(b"partial zip")
        with self.assertRaises(ScopeError):
            list(archive_members(path))

    def test_duplicate_property_and_truncated_framing_are_partial(self):
        report = inspect_scripts(model_fixture()[:-4])
        self.assertEqual(report["status"], "partial")
        self.assertTrue(report["errors"])

    def test_compressed_container_offsets_are_not_file_offsets(self):
        from compression import zstd
        from bloxscope.container_format import chunks
        raw = model_fixture()
        encoded = bytearray(raw[:32])
        import struct
        for c in chunks(raw):
            if c["tag"] == "PROP":
                packed = zstd.compress(c["payload"])
                encoded.extend(struct.pack("<4sIII", c["tag_bytes"], len(packed), len(c["payload"]), 0)+packed)
            else:
                encoded.extend(struct.pack("<4sIII", c["tag_bytes"], 0, len(c["payload"]), 0)+c["payload"])
        records = []
        extract_file("fixture.rbxm", bytes(encoded), lambda *r: records.append(r), lambda _: 1)
        for text, kind, enc, off, length, loc, sid in records:
            self.assertIsNone(loc.get("file_offset"))
            self.assertIn("chunk_uncompressed_offset", loc)
            c = next(c for c in chunks(bytes(encoded)) if c["offset"] == loc["chunk_file_offset"])
            payload = decompress(c["payload"], c["compressed_bytes"], c["unpacked_bytes"])[0]
            a = loc["chunk_uncompressed_offset"]
            self.assertEqual(payload[a:a+length].decode(enc), text)

    def test_terminal_controls_and_strict_ascii(self):
        stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii", errors="strict")
        with redirect_stdout(stream):
            say("café\x1b[31m\n漢字\u202e")
        stream.flush()
        output = stream.buffer.getvalue()
        self.assertNotIn(b"\x1b", output)
        self.assertIn(b"\\x1b", output)
        self.assertIn(b"\\u202e", output)


@unittest.skipUnless(importlib.util.find_spec("capstone"), "Optional reference tests need capstone==5.0.9")
class ReferenceTests(TempCase):
    def test_verified_instruction_and_pointer_candidate(self):
        raw = pe_fixture()
        path = self.root/"references.sqlite3"
        stats = build_references(raw, path)
        db = sqlite3.connect(path)
        try:
            rows = db.execute("SELECT target,instruction_va,file_offset,bytes,decoded FROM refs").fetchall()
        finally:
            db.close()
        self.assertEqual(stats["references"], 1)
        self.assertEqual(rows[0][:4], (0x140002000, 0x140001000, 0x200, "488d0df90f0000"))
        self.assertIn("lea", rows[0][4])
        candidates = pointer_page(raw, 0x140002000)
        self.assertEqual(candidates["count"], 1)
        self.assertEqual(candidates["candidate_pointer_data"][0]["classification"], "candidate-pointer-data")

    def test_other_architecture_is_explicitly_unsupported(self):
        raw = bytearray(pe_fixture())
        raw[0x84:0x86] = b"\x64\xaa"
        with self.assertRaises(ScopeError):
            PE(bytes(raw)).runtime_ranges()


class SourceTests(TempCase):
    def test_manifest_validation_and_official_hosts(self):
        self.assertTrue(official("https://setup.rbxcdn.com/version-example"))
        for url in ("http://setup.rbxcdn.com/a", "https://setup.rbxcdn.com.evil.test/a", "https://user@setup.rbxcdn.com/a", "https://example.com/a"):
            self.assertFalse(official(url))
        for body in (b"v1", b"v0\nx.zip\nbad\n1\n1", b"v0\n../x\n"+b"a"*32+b"\n1\n1"):
            with self.assertRaises(ScopeError):
                parse_manifest(body)

    def test_http_bounds_incomplete_bodies_and_no_auth(self):
        class Response(io.BytesIO):
            status = 200
            url = "https://setup.rbxcdn.com/synthetic-fixture"
            headers = {"Content-Length": "5"}
        class Opener:
            request = None
            def open(self, request, **kwargs):
                self.request = request
                return Response(b"ab")
        http = HTTP()
        http.opener = Opener()
        with self.assertRaises(ScopeError):
            http.fetch(Response.url, self.root/"response", 10)
        self.assertFalse(any(x.lower() in {"cookie", "authorization"} for x in http.opener.request.headers))
        with self.assertRaises(ScopeError):
            http.fetch(Response.url, self.root/"response", 1)


if __name__ == "__main__":
    unittest.main()
