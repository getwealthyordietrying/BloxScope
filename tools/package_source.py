"""Create a source-only ZIP with an explicit allowlist."""
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bloxscope.common import atomic_json, file_hash, now


def package_source():
    output = ROOT / "dist" / "BloxScope-0.1.0-source.zip"
    output.parent.mkdir(exist_ok=True)
    roots = ("bloxscope", "tests", "tools", "reports", "licenses")
    files = [ROOT / n for n in ("README.md", "pyproject.toml", "LICENSE", "CODE_REUSE.md", ".gitignore")]
    for name in roots:
        files.extend(p for p in (ROOT/name).rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts and p.suffix in {".py", ".md", ".json", ".txt"})
    temp = output.with_suffix(".tmp")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(files):
            z.write(path, "BloxScope/" + path.relative_to(ROOT).as_posix())
    temp.replace(output)
    atomic_json(output.with_suffix(".manifest.json"), {"created_at": now(), "source_sha256": file_hash(output),
                 "source_bytes": output.stat().st_size, "files": len(files),
                 "excludes": ["downloaded resources", "databases", "local indexes", "caches", "exports", "virtual environments"]})
    print(output)
    return output


if __name__ == "__main__":
    package_source()
