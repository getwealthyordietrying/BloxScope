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


def main():
    package = ROOT / "dist" / "BloxScope-0.1.0-source.zip"
    with tempfile.TemporaryDirectory(prefix="bloxscope-clean-") as tmp:
        target = Path(tmp)
        with zipfile.ZipFile(package) as z:
            names = z.namelist()
            assert all(n.startswith("BloxScope/") and ".." not in Path(n).parts for n in names)
            forbidden = {".exe", ".dll", ".sqlite3", ".zip", ".rbxm", ".rbxl", ".pyc"}
            assert not any(Path(n).suffix in forbidden or "/.bloxscope/" in n or "/.venv/" in n for n in names)
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
        help_run = subprocess.run([sys.executable, "-m", "bloxscope", "--help"], cwd=folder, env=env, capture_output=True, text=True)
        assert help_run.returncode == 0
        report = {"verified_at": now(), "origin": "synthetic-test-fixture", "fixture_data": True,
                  "source_sha256": file_hash(package), "source_bytes": package.stat().st_size,
                  "clean_extracted_folder": True, "test_exit_code": tests.returncode,
                  "test_summary": tests.stderr.splitlines()[-4:], "terminal_exit_code": session.returncode,
                  "strict_ascii": True, "reference_and_export_workflow": True,
                  "core_without_site_packages": True,
                  "resource_and_database_exclusion_verified": True,
                  "packaged_file_count": len(names)}
        atomic_json(ROOT/"verification"/"clean-release.json", report)
        atomic_json(ROOT/"verification"/"synthetic-terminal-session.json", {"origin": "synthetic-test-fixture", "fixture_data": True, "stdout": session.stdout, "stderr": session.stderr})
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
