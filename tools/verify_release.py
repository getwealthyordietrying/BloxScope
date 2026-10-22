"""Verify the source ZIP in a fresh folder. All test data is synthetic."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bloxscope.common import atomic_json, file_hash, now
from bloxscope import __version__


def main():
    package = ROOT / "dist" / f"BloxScope-{__version__}-source.zip"
    with tempfile.TemporaryDirectory(prefix="bloxscope-clean-") as tmp:
        target = Path(tmp)
        with zipfile.ZipFile(package) as z:
            names = z.namelist()
            assert all(n.startswith("BloxScope/") and ".." not in Path(n).parts for n in names)
            forbidden = {".exe", ".dll", ".sqlite3", ".zip", ".rbxm", ".rbxl", ".pyc"}
            assert not any(Path(n).suffix in forbidden or "/.bloxscope/" in n or "/.venv/" in n for n in names)
            assert "BloxScope/CODE_REUSE.md" not in names
            assert "BloxScope/reports/read-only-inputs.json" not in names
            home = str(Path.home())
            for name in names:
                text = z.read(name).decode("utf-8")
                assert home not in text and home.replace("\\", "/") not in text, name
            z.extractall(target)
        folder = target/"BloxScope"
        env = {**os.environ, "PYTHONIOENCODING": "ascii:strict", "PYTHONDONTWRITEBYTECODE": "1"}
        tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-v"], cwd=folder, env=env, capture_output=True, text=True)
        # A second terminal session exercises first launch plus reference pages
        # and export from the clean packaged source, with labelled fake HTTP.
        code = "from unittest.mock import patch; from tests.fixtures import FakeHTTP; from bloxscope.cli import main;\nwith patch('bloxscope.prepare.HTTP',FakeHTTP): raise SystemExit(main())"
        commands = "S SYNTHETIC Fixture Gift\n1\nR\nN\nP\nC\nV\nE\nB\nS 123\nH\nQ\n"
        session = subprocess.run([sys.executable, "-c", code], cwd=folder, env=env, input=commands, capture_output=True, text=True)
        exports = list((folder/".bloxscope"/"exports").glob("*.json"))
        assert tests.returncode == 0, tests.stderr
        assert session.returncode == 0, session.stderr
        for expected in ("SYNTHETIC TEST FIXTURE", "verified decoded references", "candidate pointer/data", "Saved:", "for '123'"):
            assert expected in session.stdout, expected
        assert exports and all(json.loads(p.read_text())["origin"] == "synthetic-test-fixture" for p in exports)
        core_code = code.replace("main()", "main(['--store','core-store'])")
        core = subprocess.run([sys.executable, "-S", "-c", core_code], cwd=folder, env=env,
                              input="S SYNTHETIC Fixture Gift\n1\nR\nB\nQ\n", capture_output=True, text=True)
        assert core.returncode == 0, core.stderr
        assert "Experimental references need optional capstone" in core.stdout
        desktop = target/"OneDrive"/"Desktop"
        desktop.mkdir(parents=True)
        update_code = "import sys;from pathlib import Path;from unittest.mock import patch;from tests.fixtures import FakeHTTP;from bloxscope.cli import main;\nwith patch('bloxscope.prepare.HTTP',FakeHTTP),patch('bloxscope.cli.desktop_directory',lambda:Path(sys.argv[1])): raise SystemExit(main([]))"
        update_commands = "Gift --advexport\nN\nJMP 3\nJMP 999\n31\nB\n--advexport\nY\n--export\nY\nS Gift --export\nS 123\nH\nQ\n"
        update = subprocess.run([sys.executable, "-c", update_code, str(desktop)], cwd=folder, env=env,
                                input=update_commands, capture_output=True, text=True)
        assert update.returncode == 0, update.stderr
        for expected in ("Page 3/", "Valid pages:", "Found:", "Export cancelled", "TXT size:", "Saved:", "for 'Gift --export'"):
            assert expected in update.stdout, expected
        txt_files = list(desktop.glob("*.txt"))
        assert len(txt_files) == 2
        basic, = desktop.glob("*-basic-*.txt")
        advanced, = desktop.glob("*-advanced-*.txt")
        import re
        measured = re.findall(r"TXT size: ([\d,]+) bytes", update.stdout)[-1]
        assert int(measured.replace(",", "")) == basic.stat().st_size
        assert all(b"\x1b" not in file.read_bytes() for file in txt_files)
        assert "Origin: synthetic-test-fixture" in advanced.read_text(encoding="utf-8")
        assert basic.read_text(encoding="utf-8").startswith("1. ")
        help_run = subprocess.run([sys.executable, "-m", "bloxscope", "--help"], cwd=folder, env=env, capture_output=True, text=True)
        assert help_run.returncode == 0
        report = {"verified_at": now(), "origin": "synthetic-test-fixture", "fixture_data": True,
                  "source_sha256": file_hash(package), "source_bytes": package.stat().st_size,
                  "clean_extracted_folder": True, "test_exit_code": tests.returncode,
                  "test_summary": tests.stderr.splitlines()[-4:], "terminal_exit_code": session.returncode,
                  "strict_ascii": True, "reference_and_export_workflow": True,
                  "core_without_site_packages": True,
                  "txt_jump_and_confirmation_workflow": True,
                  "txt_size_exact": True, "txt_export_bytes": basic.stat().st_size,
                  "personal_home_paths_excluded": True,
                  "resource_and_database_exclusion_verified": True,
                  "packaged_file_count": len(names)}
        atomic_json(ROOT/"verification"/f"clean-release-{__version__}.json", report)
        atomic_json(ROOT/"verification"/f"synthetic-terminal-session-{__version__}.json", {"origin": "synthetic-test-fixture", "fixture_data": True, "stdout": session.stdout, "stderr": session.stderr, "update_stdout": update.stdout})
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
