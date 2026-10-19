"""Only the two observed public LIVE products and the official CDN."""
from dataclasses import dataclass, asdict
import http.client
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .common import ScopeError, atomic_json, file_hash, now

CDN = "https://setup.rbxcdn.com"
SETTINGS = "https://clientsettingscdn.roblox.com/v2/client-version/"
PRODUCTS = {"Player": "WindowsPlayer", "Studio": "WindowsStudio64"}
EXECUTABLES = {"Player": ("RobloxApp.zip", "RobloxPlayerBeta.exe"),
               "Studio": ("RobloxStudio.zip", "RobloxStudioBeta.exe")}
RESOURCE_PACKAGES = ("ApplicationConfig.zip", "content-configs.zip", "content-models.zip",
                     "extracontent-scripts.zip", "extracontent-luapackages.zip",
                     "extracontent-models.zip", "extracontent-places.zip",
                     "extracontent-translations.zip", "studiocontent-models.zip")
MAX_DOWNLOAD = 512 * 1024**2
MAX_SELECTED_DOWNLOAD = 1024**3
MAX_SELECTED_UNPACKED = 2 * 1024**3


def official(url):
    try:
        p = urllib.parse.urlsplit(url)
        return (p.scheme == "https" and p.hostname in {"setup.rbxcdn.com", "clientsettingscdn.roblox.com"}
                and p.port in (None, 443) and p.username is None and p.password is None)
    except ValueError:
        return False


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not official(newurl):
            raise ScopeError("Redirect outside the official source hosts")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class HTTP:
    origin = "direct-official-roblox"

    def __init__(self):
        self.opener = urllib.request.build_opener(Redirects())

    def fetch(self, url, path, limit, progress=lambda _: None):
        if not official(url):
            raise ScopeError("Source URL is outside the official source hosts")
        path = Path(path)
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "BloxScope/0.1 public resource study",
                                                          "Accept-Encoding": "identity"})
                with self.opener.open(req, timeout=30) as r, path.open("wb") as f:
                    if r.status != 200 or not official(r.url):
                        raise ScopeError(f"Expected HTTP 200: {url}")
                    declared = r.headers.get("Content-Length")
                    declared = int(declared) if declared is not None else None
                    if declared is not None and not 0 <= declared <= limit:
                        raise ScopeError("Download exceeds its size bound")
                    total, tick = 0, time.monotonic()
                    while block := r.read(min(1024**2, limit - total + 1)):
                        total += len(block)
                        if total > limit:
                            raise ScopeError("Download exceeds its size bound")
                        f.write(block)
                        if time.monotonic() - tick > 2:
                            progress(f"  downloaded {total / 1024**2:.1f} MiB")
                            tick = time.monotonic()
                    if declared is not None and total != declared:
                        raise ScopeError("Incomplete HTTP body")
                    return {"requested_url": url, "final_url": r.url, "observed_at": now(),
                            "bytes": total, "http_status": r.status,
                            "etag": r.headers.get("ETag"), "last_modified": r.headers.get("Last-Modified")}
            except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
                if attempt == 2:
                    raise ScopeError(f"Download failed: {url}: {exc}") from exc
                progress(f"Request failed ({attempt+1}/3): {url}: {exc}. Retrying...")
                time.sleep(attempt + 1)
        raise AssertionError("unreachable")


@dataclass(frozen=True)
class Package:
    name: str
    md5: str
    compressed_size: int
    unpacked_size: int


def parse_manifest(raw):
    lines = [x.strip() for x in raw.decode("utf-8-sig").splitlines() if x.strip()]
    if not lines or lines[0] != "v0" or (len(lines) - 1) % 4:
        raise ScopeError("Unsupported package manifest")
    packages = {}
    for i in range(1, len(lines), 4):
        name, checksum, compressed, unpacked = lines[i:i+4]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or not re.fullmatch(r"[a-f0-9]{32}", checksum):
            raise ScopeError("Invalid package manifest record")
        try:
            p = Package(name, checksum, int(compressed), int(unpacked))
        except ValueError as exc:
            raise ScopeError("Invalid manifest size") from exc
        if min(p.compressed_size, p.unpacked_size) < 0 or name in packages:
            raise ScopeError("Duplicate package or invalid size")
        packages[name] = p
    return packages


def discover(http, root, progress=lambda _: None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    observations = []
    for product, endpoint in PRODUCTS.items():
        response = root / f"{product}-version.json"
        progress("  Checking " + product + " version and package manifest")
        vm = http.fetch(SETTINGS + endpoint, response, 1024**2, progress)
        atomic_json(root / f"{product}-version-http.json", vm)
        v = json.loads(response.read_bytes())
        build = v.get("clientVersionUpload", "")
        if not re.fullmatch(r"version-[a-f0-9]{16}", build):
            raise ScopeError("Unsupported official build identifier")
        manifest = root / f"{product}-manifest.txt"
        mm = http.fetch(f"{CDN}/{build}-rbxPkgManifest.txt", manifest, 1024**2, progress)
        atomic_json(root / f"{product}-manifest-http.json", mm)
        packages = parse_manifest(manifest.read_bytes())
        wanted = (EXECUTABLES[product][0],) + RESOURCE_PACKAGES
        if EXECUTABLES[product][0] not in packages or "extracontent-models.zip" not in packages:
            raise ScopeError(f"{product}: required executable/model package is missing")
        selected = [asdict(packages[n]) for n in wanted if n in packages]
        observations.append({"product": product, "build": build, "version": v,
                             "version_source": vm, "version_sha256": file_hash(response),
                             "manifest_source": mm, "manifest_sha256": file_hash(manifest),
                             "selected": selected, "absent": [n for n in wanted if n not in packages],
                             "omitted": [n for n in packages if n not in wanted]})
    compressed = sum(p["compressed_size"] for o in observations for p in o["selected"])
    unpacked = sum(p["unpacked_size"] for o in observations for p in o["selected"])
    if compressed > MAX_SELECTED_DOWNLOAD or unpacked > MAX_SELECTED_UNPACKED:
        raise ScopeError("Selected packages exceed the documented collection bounds. No scope expansion was made.")
    return observations


def package(http, root, build, p, progress):
    """A completed package is a durable checkpoint. Partial HTTP bodies restart."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / (p["md5"] + ".zip")
    meta_path = path.with_suffix(".json")
    requested = f"{CDN}/{build}-{p['name']}"
    if path.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text())
        if (path.stat().st_size == p["compressed_size"] and file_hash(path, "md5") == p["md5"]
                and file_hash(path) == meta["sha256"]):
            return path, {**meta, "requested_url": requested, "cache_hit": True,
                          "verified_at": now(), "manifest": p}
        raise ScopeError(f"Cached package is damaged: {path}. Move it aside before retrying.")
    partial = path.with_suffix(".part")
    meta = http.fetch(requested, partial, min(p["compressed_size"], MAX_DOWNLOAD), progress)
    if partial.stat().st_size != p["compressed_size"] or file_hash(partial, "md5") != p["md5"]:
        raise ScopeError("Package size/MD5 does not match the official manifest: " + p["name"])
    meta.update(sha256=file_hash(partial), md5=p["md5"], download_url=requested, cache_hit=False,
                verified_at=now(), manifest=p)
    partial.replace(path)
    atomic_json(meta_path, meta)
    return path, meta
