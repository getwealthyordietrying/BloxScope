"""Create a source-only ZIP with an explicit allowlist."""
from pathlib import Path
import json
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bloxscope.common import atomic_json, file_hash, now
from bloxscope import __version__

REPORTS = {"FEASIBILITY.md", "real-reference-investigation.json", "real-terminal-verification.json",
           "real-verification.json", f"UPDATE-{__version__}.md", f"REFINEMENT-{__version__}.md"}


def public_report(path):
    """Only machine-local paths are redacted; evidence hashes/offsets stay exact."""
    value = path.read_text(encoding="utf-8")
    home = str(Path.home())
    if path.suffix == ".json":
        def clean(item):
            if isinstance(item, dict):
                return {key: clean(value) for key, value in item.items()}
            if isinstance(item, list):
                return [clean(value) for value in item]
            if isinstance(item, str):
                return item.replace(home, "<USER_HOME>").replace(home.replace("\\", "/"), "<USER_HOME>")
            return item
        return (json.dumps(clean(json.loads(value)), indent=2, ensure_ascii=True)+"\n").encode()
    return value.replace(home, "<USER_HOME>").replace(home.replace("\\", "/"), "<USER_HOME>").encode()


def package_source():
    output = ROOT / "dist" / f"BloxScope-{__version__}-source.zip"
    output.parent.mkdir(exist_ok=True)
    roots = ("bloxscope", "tests", "tools", "reports", "licenses")
    files = [ROOT / n for n in ("README.md", "pyproject.toml", "LICENSE", ".gitignore")]
    for name in roots:
        files.extend(p for p in (ROOT/name).rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts and p.suffix in {".py", ".md", ".json", ".txt"})
    files = [p for p in files if p.parent != ROOT/"reports" or p.name in REPORTS]
    temp = output.with_suffix(".tmp")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(files):
            name = "BloxScope/" + path.relative_to(ROOT).as_posix()
            if path.parent == ROOT/"reports":
                z.writestr(name, public_report(path))
            else:
                z.write(path, name)
    temp.replace(output)
    atomic_json(output.with_suffix(".manifest.json"), {"created_at": now(), "source_sha256": file_hash(output),
                 "source_bytes": output.stat().st_size, "files": len(files),
                 "excludes": ["downloaded resources", "databases", "local indexes", "caches", "exports", "virtual environments"]})
    print(output)
    return output


if __name__ == "__main__":
    package_source()
