import argparse
from libs.pack_downloader import *

import zipfile
import os
from pathlib import Path
import platform
import subprocess
import hashlib
from javaproperties import Properties
import shutil
import sys
import traceback

# Load config
config = Properties()
if not os.path.exists("config.properties"):
    shutil.copyfile("config.template.properties", "config.properties")
with open("config.properties", "rb") as f:
    config.load(f)

# ---------------- CACHE HELPERS ----------------

def safe_download(session, url, path: Path):
    """
    Downloads a file only if it doesn't already exist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists() and path.stat().st_size > 0:
        return False

    r = session.get(url)
    r.raise_for_status()
    path.write_bytes(r.content)
    return True


def file_exists(path: Path):
    return path.exists() and path.stat().st_size > 0

def asset_exists(asset_path: Path, expected_hash: str):
    if not asset_path.exists():
        return False

    # optional but safer: verify hash
    h = hashlib.sha1()
    with open(asset_path, "rb") as f:
        h.update(f.read())

    return h.hexdigest() == expected_hash

# ---------------- RULES ----------------

def check_rules(rules):
    if not rules:
        return True

    os_name = platform.system().lower()
    if os_name == "darwin":
        os_name = "osx"

    allowed = False

    for rule in rules:
        action = rule.get("action")

        if "os" in rule:
            rule_os = rule["os"].get("name")
            if rule_os and rule_os != os_name:
                continue

        if action == "allow":
            allowed = True
        elif action == "disallow":
            return False

    return allowed


# ---------------- NATIVES ----------------

def extract_natives(lib, lib_path, natives_dir):
    if "natives" not in lib:
        return

    os_name = platform.system().lower()
    if os_name == "darwin":
        os_name = "osx"

    natives_map = lib.get("natives", {})
    native_os_key = natives_map.get(os_name)

    if not native_os_key:
        return

    with zipfile.ZipFile(lib_path, "r") as jar:
        for file in jar.namelist():
            if file.endswith((".dll", ".so", ".dylib")):
                out_path = os.path.join(natives_dir, os.path.basename(file))

                if file_exists(Path(out_path)):
                    continue

                with open(out_path, "wb") as f:
                    f.write(jar.read(file))


# ---------------- DOWNLOAD GAME ----------------

def download_game(version, workers=30):
    base_dir = Path("minecraft_runtime")
    version_dir = base_dir / "versions" / version
    libraries_dir = base_dir / "libraries"
    assets_dir = base_dir / "assets"
    natives_dir = version_dir / "natives"

    version_dir.mkdir(parents=True, exist_ok=True)
    libraries_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)
    natives_dir.mkdir(parents=True, exist_ok=True)

    print("Downloading manifest...")
    manifest = grab_minecraft_manifest()

    version_info = next(
        (v for v in manifest["versions"] if v["id"] == version),
        None
    )

    if not version_info:
        raise ValueError(f"Version {version} not found")

    print("Downloading version JSON...")
    version_json = grab_version_json(version_info)

    # ---------------- ASSETS ----------------
    print("Downloading assets...")
    asset_index_url = version_json["assetIndex"]["url"]

    r = session.get(asset_index_url)
    r.raise_for_status()
    asset_index = r.json()

    objects = asset_index.get("objects", {})

    downloaded = 0
    skipped = 0

    for name, obj in objects.items():
        hash_val = obj["hash"]
        size = obj["size"]

        subdir = hash_val[:2]
        asset_path = assets_dir / "objects" / subdir / hash_val

        url = f"https://resources.download.minecraft.net/{subdir}/{hash_val}"

        if asset_path.exists() and asset_path.stat().st_size == size:
            skipped += 1
            continue

        asset_path.parent.mkdir(parents=True, exist_ok=True)

        r = session.get(url)
        r.raise_for_status()

        asset_path.write_bytes(r.content)
        downloaded += 1

    # ---------------- CLIENT JAR ----------------
    print("Checking client jar...")
    jar_url = version_json["downloads"]["client"]["url"]

    jar_path = version_dir / f"{version}.jar"

    if safe_download(session, jar_url, jar_path):
        print("Downloaded client jar")
    else:
        print("Client jar already exists, skipping")

    # ---------------- LIBRARIES ----------------
    print("Downloading libraries...")
    classpath = []

    for lib in version_json["libraries"]:
        if "downloads" not in lib:
            continue

        if not check_rules(lib.get("rules", [])):
            continue

        artifact = lib["downloads"].get("artifact")
        if not artifact:
            continue

        lib_path = libraries_dir / artifact["path"]
        lib_path.parent.mkdir(parents=True, exist_ok=True)

        classpath.append(str(lib_path))

        if safe_download(session, artifact["url"], lib_path):
            print(f"Downloaded {artifact['path']}")
        else:
            print(f"Skipped {artifact['path']}")

        # natives
        if "natives" in lib:
            extract_natives(lib, lib_path, natives_dir)

    classpath.append(str(jar_path))

    print("Done downloading Minecraft.")

    return {
        "version_json": version_json,
        "classpath": classpath,
        "natives_dir": str(natives_dir),
        "assets_dir": str(assets_dir),
        "version_dir": str(version_dir)
    }


# ---------------- START MINECRAFT ----------------

def start_minecraft(info, version, username="Player", java_path="java"):
    version_json = info["version_json"]
    classpath = info["classpath"]
    natives_dir = info["natives_dir"]

    game_dir = info.get("game_dir", "minecraft")
    assets_dir = info.get("assets_dir", "assets")

    main_class = version_json.get("mainClass")
    if not main_class:
        raise ValueError("Missing mainClass")

    # asset index is usually inside version json
    asset_index = version_json.get("assetIndex", {}).get("id", version)

    sep = ";" if platform.system().lower() == "windows" else ":"
    cp = sep.join(classpath)

    cmd = [
        java_path,
        f"-Djava.library.path={natives_dir}",
        "-Xmx2G",
        "-Xms1G",
        "-cp",
        cp,
        main_class,

        "--username", username,
        "--uuid", "00000000-0000-0000-0000-000000000000",
        "--accessToken", "0",
        "--version", version,
        "--gameDir", game_dir,
        "--assetsDir", assets_dir,
        "--assetIndex", asset_index,
        "--userType", "legacy"
    ]

    print("\nLaunching Minecraft...\n")

    subprocess.run(cmd)


# ---------------- MAIN ----------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("version", type=str)
    parser.add_argument("-w", "--workers", type=int, default=30)
    parser.add_argument("--dont-run", action="store_false", dest="run")

    args = parser.parse_args()

    try:
        info = download_game(args.version, workers=args.workers)

        if args.run:
            start_minecraft(info, username=config["username"], java_path=config["java_path"], version=args.version)
    except KeyboardInterrupt:
        print("\nInterrupted by user. Stopping...")
    except KeyError:
        exc_type, exc_value, exc_tb = sys.exc_info()
        tb = traceback.extract_tb(exc_tb)
    
        if any("propclass.py" in frame.filename for frame in tb):
            print(f"Error! Invalid config file. Missing: {exc_value}")
        else:
            raise