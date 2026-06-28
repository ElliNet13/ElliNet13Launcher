import argparse
from libs.pack_downloader import *
import zipfile
import os
from pathlib import Path
import platform

def check_rules(rules):
    if not rules:
        return True  # no rules = always allowed

    os_name = platform.system().lower()
    if os_name == "darwin":
        os_name = "osx"

    allowed = False

    for rule in rules:
        action = rule.get("action")

        # default OS filter
        if "os" in rule:
            rule_os = rule["os"].get("name")

            if rule_os and rule_os != os_name:
                continue

        if action == "allow":
            allowed = True
        elif action == "disallow":
            return False

    return allowed

def extract_natives(lib, lib_path, natives_dir):
    if "natives" not in lib:
        return

    native_os_key = None

    # detect OS key (windows/linux/osx)
    import platform
    os_name = platform.system().lower()
    if os_name == "darwin":
        os_name = "osx"

    natives_map = lib.get("natives", {})
    native_os_key = natives_map.get(os_name)

    if not native_os_key:
        return

    with zipfile.ZipFile(lib_path, "r") as jar:
        for file in jar.namelist():
            # only native binaries, not .class files
            if file.endswith((".dll", ".so", ".dylib")):
                # replace placeholder like ${arch}
                out_name = native_os_key.replace("${arch}", platform.machine().lower())
                out_path = os.path.join(natives_dir, os.path.basename(file))

                with open(out_path, "wb") as f:
                    f.write(jar.read(file))

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

    download_assets(asset_index, str(assets_dir), workers=workers)

    # ---------------- CLIENT JAR ----------------
    print("Downloading client jar...")
    jar_url = version_json["downloads"]["client"]["url"]

    jar_path = version_dir / f"{version}.jar"
    r = session.get(jar_url)
    r.raise_for_status()

    jar_path.write_bytes(r.content)

    # ---------------- LIBRARIES ----------------
    print("Downloading libraries...")
    classpath = []

    for lib in version_json["libraries"]:
        if "downloads" not in lib:
            continue

        # Skip libraries with rules (basic support)
        if not check_rules(lib.get("rules", [])):
            continue

        artifact = lib["downloads"].get("artifact")
        if not artifact:
            continue

        lib_path = libraries_dir / artifact["path"]
        lib_path.parent.mkdir(parents=True, exist_ok=True)

        r = session.get(artifact["url"])
        r.raise_for_status()
        lib_path.write_bytes(r.content)

        classpath.append(str(lib_path))

        # natives
        if "natives" in lib["downloads"]:
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

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Minecraft assets + packs.")
    parser.add_argument("version", type=str)
    parser.add_argument("-w", "--workers", type=int, default=30, help="Number of threads to use for downloading assets.")
    args = parser.parse_args()

    download_game(args.version, workers=args.workers)