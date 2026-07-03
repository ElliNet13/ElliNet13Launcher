import argparse
from libs.pack_downloader import *
from libs import omniarchive as omniarchive_lib

import threading
import zipfile
import os
from pathlib import Path
import platform
import subprocess
import hashlib
from javaproperties import PropertiesFile
import shutil
import sys
import traceback
from tqdm import tqdm
from libs.mcauth3 import MCMSA
from typing import Tuple, Optional
import customtkinter as ctk
import webbrowser
from concurrent.futures import Future
import time
import zlib
import json

class UserError(Exception):
    """Raise to show a error to the user when in GUI mode."""
    pass

# Load config
configfile = "config.properties"
if not os.path.exists("config.properties"):
    shutil.copyfile("config.template.properties", "config.properties")
with open(configfile, "r", encoding="latin-1") as f:
    config = PropertiesFile.load(f)

def save_config():
    with open(configfile, "w", encoding="latin-1") as f:
        config.dump(f)

def save_list(arr: list) -> str:
    return zlib.compress(json.dumps(arr, separators=(",", ":")).encode("utf-8")).decode("latin-1")

def load_list(arr: str) -> list:
    return json.loads(zlib.decompress(arr.encode("latin-1")).decode("utf-8"))

# ---------------- OMNIARCHIVE ----------------
OMNIARCHIVE_INDEX_FILE = "omniarchive.json"

def load_omniarchive_index():
    """Load the local Omniarchive index if it exists."""
    if os.path.exists(OMNIARCHIVE_INDEX_FILE):
        with open(OMNIARCHIVE_INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def scrape_omniarchive_index():
    """Scrape the Omniarchive index and save it locally."""
    print("Scraping Omniarchive index...")
    omniarchive_lib.scrape_omniarchive()
    return load_omniarchive_index()

def find_omniarchive_version(version: str):
    """
    Try to find a version in Omniarchive.
    Returns the download URL if found, None otherwise.
    """
    index = load_omniarchive_index()
    if not index:
        return None

    # First try exact match
    for era, versions in index.items():
        if version in versions:
            return versions[version]

    # Try case-insensitive match
    version_lower = version.lower()
    for era, versions in index.items():
        for v in versions:
            if v.lower() == version_lower:
                return versions[v]

    return None

def get_all_omniarchive_versions():
    """Get a sorted list of all versions available in Omniarchive."""
    index = load_omniarchive_index()
    if not index:
        return []

    all_versions = []
    for era, versions in index.items():
        for v in versions:
            if v not in all_versions:
                all_versions.append(v)

    # Sort versions (newest first)
    def version_sort_key(v):
        # Extract version numbers for proper sorting
        parts = v.replace("-snapshot", "").replace("-pre", "").replace("-rc", "").replace("-beta", "").replace("-alpha", "").split(".")
        try:
            return [int(p) if p.isdigit() else 0 for p in parts] + [0, 0]
        except:
            return [0, 0, 0]

    all_versions.sort(key=version_sort_key, reverse=True)
    return all_versions

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

def check_rules(rules, is_demo=False, has_custom_resolution=False, has_quick_plays_support=False, is_quick_play_singleplayer=False, is_quick_play_multiplayer=False, is_quick_play_realms=False):
    """
    Check if rules allow the argument to be included.

    For feature-based rules, only include the argument if the feature condition is met.
    Default is to NOT include arguments with feature requirements unless conditions are explicitly true.
    """
    if not rules:
        return True

    os_name = platform.system().lower()
    if os_name == "darwin":
        os_name = "osx"

    allowed = False

    for rule in rules:
        action = rule.get("action")

        # Check OS-based rules
        if "os" in rule:
            rule_os = rule["os"].get("name")
            if rule_os and rule_os != os_name:
                continue

            # Check for OS version constraints
            rule_os_version = rule["os"].get("version")
            if rule_os_version:
                # For now, skip OS version checks (would need more complex evaluation)
                continue

        # Check feature-based rules
        if "features" in rule:
            features = rule["features"]

            # Check each feature condition
            if "is_demo_user" in features:
                if not features["is_demo_user"] == is_demo:
                    continue

            if "has_custom_resolution" in features:
                if not features["has_custom_resolution"] == has_custom_resolution:
                    continue

            if "has_quick_plays_support" in features:
                if not features["has_quick_plays_support"] == has_quick_plays_support:
                    continue

            if "is_quick_play_singleplayer" in features:
                if not features["is_quick_play_singleplayer"] == is_quick_play_singleplayer:
                    continue

            if "is_quick_play_multiplayer" in features:
                if not features["is_quick_play_multiplayer"] == is_quick_play_multiplayer:
                    continue

            if "is_quick_play_realms" in features:
                if not features["is_quick_play_realms"] == is_quick_play_realms:
                    continue

        if action == "allow":
            allowed = True
        elif action == "disallow":
            return False

    return allowed


def parse_jvm_arguments(version_json, natives_dir, classpath, game_dir, assets_dir, asset_index, launcher_name="ElliNet13Launcher", launcher_version="1.0", max_memory="2G", min_memory="1G", logging_config_path=None):
    """
    Parse JVM arguments from version JSON and substitute placeholders.
    """
    jvm_args = []

    arguments = version_json.get("arguments", {})
    jvm_arguments = arguments.get("jvm", [])

    # Build substitution map
    sep = ";" if platform.system().lower() == "windows" else ":"
    cp = sep.join(classpath)

    subs = {
        "natives_directory": natives_dir,
        "classpath": cp,
        "game_directory": game_dir,
        "assets_root": assets_dir,
        "assets_index_name": asset_index,
        "launcher_name": launcher_name,
        "launcher_version": launcher_version,
    }

    # Add logging config path if available
    if logging_config_path:
        subs["path"] = logging_config_path
    else:
        subs["path"] = ""

    for arg in jvm_arguments:
        if isinstance(arg, str):
            # Substitute placeholders
            result = arg
            for key, value in subs.items():
                result = result.replace(f"${{{key}}}", value)
            jvm_args.append(result)
        elif isinstance(arg, dict):
            # Has rules - check if we should include it
            if check_rules(arg.get("rules", [])):
                value = arg.get("value")
                if isinstance(value, list):
                    for v in value:
                        if isinstance(v, str):
                            result = v
                            for key, val in subs.items():
                                result = result.replace(f"${{{key}}}", val)
                            jvm_args.append(result)
                        else:
                            jvm_args.append(str(v))
                elif isinstance(value, str):
                    result = value
                    for key, val in subs.items():
                        result = result.replace(f"${{{key}}}", val)
                    jvm_args.append(result)

    # Add memory args (Mojang doesn't provide these - launcher handles them)
    jvm_args.append(f"-Xmx{max_memory}")
    jvm_args.append(f"-Xms{min_memory}")

    return jvm_args


def parse_game_arguments(version_json, version, game_dir, assets_dir, asset_index, username, uuid, access_token, client_id=None, xuid=None, version_type="release", is_demo=False, width=None, height=None):
    """
    Parse game arguments from version JSON and substitute placeholders.
    """
    game_args = []

    arguments = version_json.get("arguments", {})
    game_arguments = arguments.get("game", [])

    subs = {
        "auth_player_name": username,
        "version_name": version,
        "game_directory": game_dir,
        "assets_root": assets_dir,
        "assets_index_name": asset_index,
        "auth_uuid": uuid,
        "auth_access_token": access_token,
        "clientid": client_id or "",
        "auth_xuid": xuid or "",
        "version_type": version_type,
        "resolution_width": str(width) if width else "0",
        "resolution_height": str(height) if height else "0",
    }

    for arg in game_arguments:
        if isinstance(arg, str):
            # Substitute placeholders
            result = arg
            for key, value in subs.items():
                result = result.replace(f"${{{key}}}", value)
            # Only add if substitution worked (not a literal placeholder that wasn't replaced)
            if "${" not in result:
                # Split by spaces to get individual arguments
                for part in result.split():
                    if part.strip():
                        game_args.append(part)
        elif isinstance(arg, dict):
            # Has rules - check if we should include it
            if check_rules(arg.get("rules", [])):
                value = arg.get("value")
                if isinstance(value, list):
                    for v in value:
                        if isinstance(v, str):
                            result = v
                            for key, val in subs.items():
                                result = result.replace(f"${{{key}}}", val)
                            if "${" not in result:
                                # Split by spaces to get individual arguments
                                for part in result.split():
                                    if part.strip():
                                        game_args.append(part)
                        else:
                            game_args.append(str(v))
                elif isinstance(value, str):
                    result = value
                    for key, val in subs.items():
                        result = result.replace(f"${{{key}}}", val)
                    if "${" not in result:
                        # Split by spaces to get individual arguments
                        for part in result.split():
                            if part.strip():
                                game_args.append(part)

    return game_args


# ---------------- NATIVES ----------------

def should_extract_file(file_path, extract_rules):
    """
    Check if a file should be extracted based on exclude rules.
    extract_rules example: {"exclude": ["META-INF/"]}
    """
    if not extract_rules:
        return True

    exclude_patterns = extract_rules.get("exclude", [])
    if not exclude_patterns:
        return True

    for pattern in exclude_patterns:
        if file_path.startswith(pattern) or file_path.startswith("/" + pattern):
            return False
    return True


def extract_natives(lib, natives_dir, session):
    """
    Extract native libraries from classifier JARs.

    For both legacy and modern versions, natives are stored in separate classifier JARs,
    not in the main artifact. The library's 'natives' field maps OS names to classifier keys,
    and 'downloads.classifiers' contains the actual native JAR URLs.
    """
    if "natives" not in lib:
        return

    os_name = platform.system().lower()
    if os_name == "darwin":
        os_name = "osx"

    natives_map = lib.get("natives", {})
    native_classifier_key = natives_map.get(os_name)

    if not native_classifier_key:
        return

    # Get the classifiers
    downloads = lib.get("downloads", {})
    classifiers = downloads.get("classifiers", {})

    classifier_info = classifiers.get(native_classifier_key)
    if not classifier_info:
        return

    # Download the native classifier JAR
    classifier_url = classifier_info.get("url")
    classifier_path = classifier_info.get("path")

    if not classifier_url or not classifier_path:
        return

    # Use libraries_dir for storing the classifier JAR
    libraries_dir = Path("minecraft_runtime/libraries")
    classifier_jar_path = libraries_dir / classifier_path

    # Download if needed
    if safe_download(session, classifier_url, classifier_jar_path):
        print(f"Downloaded native classifier: {classifier_path}")
    else:
        print(f"Skipped native classifier: {classifier_path}")

    # Get extract rules (some libraries have exclusions like META-INF/)
    extract_rules = lib.get("extract")

    # Extract native files from the classifier JAR
    with zipfile.ZipFile(classifier_jar_path, "r") as jar:
        for file in jar.namelist():
            if not should_extract_file(file, extract_rules):
                continue

            if file.endswith((".dll", ".so", ".dylib")):
                out_path = os.path.join(natives_dir, os.path.basename(file))

                if file_exists(Path(out_path)):
                    continue

                with open(out_path, "wb") as f:
                    f.write(jar.read(file))


# ---------------- FABRIC ----------------

def get_fabric_loader_version(minecraft_version: str):
    """Get the latest Fabric loader version compatible with a Minecraft version."""
    url = f"https://meta.fabricmc.net/v2/versions/loader/{minecraft_version}"
    r = session.get(url)
    r.raise_for_status()
    versions = r.json()

    if not versions:
        raise ValueError(f"No Fabric version found for Minecraft {minecraft_version}")

    # First entry is the latest stable version
    latest = versions[0]
    return latest["loader"]["version"], latest["launcherMeta"]["mainClass"]["client"], latest["launcherMeta"]["libraries"]

def download_fabric_loader(minecraft_version: str, base_dir: Path, libraries_dir: Path, version_dir: Path):
    """Download Fabric loader and installer JARs."""
    loader_ver, fabric_main_class, launcher_libs = get_fabric_loader_version(minecraft_version)

    print(f"Downloading Fabric loader {loader_ver} for Minecraft {minecraft_version}...")

    # Download Fabric loader JAR from Maven
    fabric_url = f"https://maven.fabricmc.net/net/fabricmc/fabric-loader/{loader_ver}/fabric-loader-{loader_ver}.jar"
    fabric_jar_path = version_dir / "fabric-loader.jar"
    if not fabric_jar_path.exists():
        r = session.get(fabric_url)
        r.raise_for_status()
        fabric_jar_path.write_bytes(r.content)
        print(f"Downloaded Fabric loader JAR")

    # Download required libraries from launcher metadata
    all_libs = launcher_libs.get("client", []) + launcher_libs.get("common", []) + launcher_libs.get("server", [])
    fabric_lib_paths = []
    for lib in all_libs:
        lib_name = lib["name"]
        # Convert maven name to path: net.fabricmc:fabric-loader:0.19.3 -> net/fabricmc/fabric-loader/0.19.3/fabric-loader-0.19.3.jar
        parts = lib_name.split(":")
        if len(parts) >= 3:
            group_path = parts[0].replace(".", "/")
            artifact = parts[1]
            version = parts[2]
            lib_filename = f"{artifact}-{version}.jar"
            # Use string concatenation to avoid Path URL encoding issues
            lib_path_str = f"{group_path}/{artifact}/{version}/{lib_filename}"

            lib_url = f"https://maven.fabricmc.net/{lib_path_str}"
            lib_dest = libraries_dir / lib_path_str
            fabric_lib_paths.append(str(lib_dest))

            if not lib_dest.exists():
                r = session.get(lib_url)
                r.raise_for_status()
                lib_dest.parent.mkdir(parents=True, exist_ok=True)
                lib_dest.write_bytes(r.content)
                print(f"Downloaded Fabric lib: {lib_name}")

    return str(fabric_jar_path), loader_ver, fabric_main_class, fabric_lib_paths

# ---------------- DOWNLOAD GAME ----------------

def download_game(version, workers=30, use_fabric=False, use_omniarchive=False):
    base_dir = Path("minecraft_runtime")
    version_dir = base_dir / "versions" / version
    libraries_dir = base_dir / "libraries"
    assets_dir = base_dir / "assets"
    java_runtime_dir = base_dir / "runtimes"
    natives_dir = version_dir / "natives"

    version_dir.mkdir(parents=True, exist_ok=True)
    libraries_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)
    java_runtime_dir.mkdir(parents=True, exist_ok=True)
    natives_dir.mkdir(parents=True, exist_ok=True)

    omniarchive_jar_url = None
    version_info = None
    print("Downloading manifest...")
    manifest = grab_minecraft_manifest()
    version_info = next(
        (v for v in manifest["versions"] if v["id"] == version),
        None
    )
    print("Downloading version JSON...")
    version_json = grab_version_json(version_info)

    should_download = config.get("download_game", "true").lower() not in {"false", "0", "no", "off"}
    logging_config_path = None
    fabric_loader_path = None
    fabric_loader_version = None
    fabric_main_class = None
    fabric_lib_paths = []

    if should_download:
        if not version_info:
            # Try Omniarchive if not in official manifest
            if use_omniarchive:
                omniarchive_jar_url = find_omniarchive_version(version)
                if omniarchive_jar_url:
                    print(f"Version {version} not in official manifest, using Omniarchive...")
                    # For Omniarchive, we need to find a nearby version for libraries/assets
                    # Find the closest version from official manifest
                    all_versions = [v["id"] for v in manifest["versions"]]
                    closest_version = None
                    for v in reversed(all_versions):
                        # Try to find a version that might have the same assets
                        if v < version:
                            closest_version = v
                            break
                    if not closest_version:
                        closest_version = manifest["latest"]["release"]

                    # Get version JSON from closest version
                    version_info = next(
                        (v for v in manifest["versions"] if v["id"] == closest_version),
                        None
                    )
                    if not version_info:
                        raise ValueError(f"Could not find closest version for {version}")
                    print(f"Using {closest_version} as base for libraries and assets...")
                else:
                    raise ValueError(f"Version {version} not found")
            else:
                raise ValueError(f"Version {version} not found")

        # Download Fabric if enabled
        fabric_loader_path = None
        fabric_loader_version = None
        fabric_main_class = None
        fabric_lib_paths = []
        if use_fabric:
            try:
                fabric_loader_path, fabric_loader_version, fabric_main_class, fabric_lib_paths = download_fabric_loader(
                    version, base_dir, libraries_dir, version_dir
                )
                print(f"Fabric {fabric_loader_version} downloaded for {version}")
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 400:
                    raise UserError(f"Fabric loader not found for Minecraft {version}.")
        # ---------------- ASSETS ----------------
        print("Downloading assets...")

        asset_index_url = version_json["assetIndex"]["url"]
        r = session.get(asset_index_url)
        r.raise_for_status()
        asset_index = r.json()

        indexes_dir = assets_dir / "indexes"
        indexes_dir.mkdir(parents=True, exist_ok=True)

        index_id = version_json["assetIndex"]["id"]
        index_path = indexes_dir / f"{index_id}.json"

        if not index_path.exists():
            with open(index_path, "w", encoding="utf-8") as f:
                import json
                json.dump(asset_index, f)

        download_assets(
            asset_index,
            output_dir=str(assets_dir),
            workers=workers
        )

        # ---------------- CLIENT JAR ----------------
        print("Checking client jar...")

        # Use Omniarchive URL if available, otherwise use official
        if omniarchive_jar_url:
            jar_url = omniarchive_jar_url
            print(f"Using Omniarchive JAR: {jar_url}")
        else:
            jar_url = version_json["downloads"]["client"]["url"]

        jar_path = version_dir / f"{version}.jar"

        if safe_download(session, jar_url, jar_path):
            print("Downloaded client jar")
        else:
            print("Client jar already exists, skipping")

        # ---------------- LOGGING CONFIG ----------------
        logging_config_path = None
        if "logging" in version_json and "client" in version_json["logging"]:
            logging_info = version_json["logging"]["client"]
            if "file" in logging_info:
                logging_file = logging_info["file"]
                logging_url = logging_file["url"]
                logging_config_path = version_dir / logging_file["id"]
                if safe_download(session, logging_url, logging_config_path):
                    print(f"Downloaded logging config: {logging_file['id']}")
                else:
                    print("Logging config already exists, skipping")

        # ---------------- LIBRARIES ----------------
        print("Downloading libraries...")
        classpath = []

        for lib in tqdm(version_json["libraries"], desc="Libraries"):
            if "downloads" not in lib:
                continue

            if not check_rules(lib.get("rules", [])):
                continue

            downloads = lib["downloads"]
            artifact = downloads.get("artifact")

            # Handle libraries with artifacts (main JARs)
            if artifact:
                lib_path = libraries_dir / artifact["path"]
                lib_path.parent.mkdir(parents=True, exist_ok=True)

                classpath.append(str(lib_path))

                if safe_download(session, artifact["url"], lib_path):
                    print(f"Downloaded {artifact['path']}")
                else:
                    print(f"Skipped {artifact['path']}")

            # Handle libraries with only classifiers (natives-only libraries)
            # These don't have an artifact, just classifiers for platform-specific natives
            # This is common in old versions (rd-132211, b1.0, 1.0, etc.)
            if "natives" in lib and not artifact:
                extract_natives(lib, natives_dir, session)
            # Also extract natives for libraries that have both artifact and natives
            elif "natives" in lib and artifact:
                extract_natives(lib, natives_dir, session)

        classpath.append(str(jar_path))

        # ---------------- JAVA RUNTIME ----------------
        print("Downloading java runtime...")
        java_runtime_dir = java_runtime_dir / version_json["javaVersion"]["component"]
        download_java_runtime(version_json, java_runtime_dir)

        print("Done downloading Minecraft.")

        final_java_path = str(java_runtime_dir / "bin/java")

        if platform.system() == "Windows":
            final_java_path += ".exe"
    else:
        print("Using cached Minecraft installation.")
        classpath = []

        for lib in version_json.get("libraries", []):
            if "downloads" not in lib:
                continue

            if not check_rules(lib.get("rules", [])):
                continue

            artifact = lib["downloads"].get("artifact")
            if artifact:
                lib_path = libraries_dir / artifact["path"]
                if lib_path.exists():
                    classpath.append(str(lib_path))

        jar_path = version_dir / f"{version}.jar"
        if jar_path.exists():
            classpath.append(str(jar_path))

        runtime_dir = java_runtime_dir / version_json["javaVersion"]["component"]
        final_java_path = str(runtime_dir / "bin/java")

        if platform.system() == "Windows":
            final_java_path += ".exe"

    return {
        "version_json": version_json,
        "classpath": classpath,
        "natives_dir": str(natives_dir),
        "assets_dir": str(assets_dir),
        "version_dir": str(version_dir),
        "java_path": final_java_path,
        "logging_config_path": str(logging_config_path) if logging_config_path else None,
        "fabric_loader_path": fabric_loader_path,
        "fabric_loader_version": fabric_loader_version,
        "fabric_main_class": fabric_main_class,
        "fabric_lib_paths": fabric_lib_paths,
        "use_fabric": use_fabric,
        "use_omniarchive": use_omniarchive,
    }


# Microsoft auth flow
def auth_microsoft(auth: MCMSA) -> Tuple[Optional[str], Optional[str]]:
                # Get device code for user verification
            print("=== Minecraft Authentication ===")
            device_info = auth.start_auth()

            print(f"\n1. Open your browser and go to:")
            print(f"   {device_info['verification_uri']}")
            print(f"\n2. Enter this code:")
            print(f"   {device_info['user_code']}")
            print(f"\n3. The code expires in {device_info['expires_in']//60} minutes")

            # Wait for user to complete verification
            input("\nPress Enter after you've completed the verification in your browser...")
            # 4. Complete authentication
            try:
                result = auth.finish_auth(device_info)

                # 5. Use the authentication result
                print(f"\n[!] Authentication Successful!")
                print(f"   Player: {result['profile']['name']}")
                print(f"   UUID: {result['profile']['id']}")

                config["do_not_share_-_microsoft_account_refresh_token"] = result['tokens']['microsoft_refresh_token']
                save_config()

                minecraft_access_token = result["tokens"]["minecraft_access_token"]
                profile = result["profile"]

                return minecraft_access_token, profile

            except Exception as e:
                print(f"\n[X] Authentication failed: {e}")
                return None, None
            

# ---------------- VERIFY ----------------
def verify_version(version, use_omniarchive=False):
    # Always check official manifest first
    manifest = grab_minecraft_manifest()

    versions = manifest.get("versions", [])

    for v in versions:
        if v.get("id") == version:
            return True

    # If not found and omniarchive is enabled, check Omniarchive
    if use_omniarchive:
        omni_url = find_omniarchive_version(version)
        if omni_url:
            return True

    return False

# ---------------- START MINECRAFT ----------------
def start_minecraft(info, version, username="Player", auth: MCMSA|None = None, profile:None|dict=None, minecraft_access_token:None|str=None, client_id: str | None = None, xuid: str | None = None, use_fabric: bool = None, game_dir: str = "minecraft"): # type: ignore
    version_json = info["version_json"]
    classpath = info["classpath"]
    natives_dir = info["natives_dir"]

    assets_dir = info.get("assets_dir", "assets")

    if not os.path.isabs(game_dir):
        game_dir = str(Path.cwd() / "minecraft" / game_dir)

    Path(game_dir).mkdir(parents=True, exist_ok=True)

    # Determine if we should use Fabric
    if use_fabric is None:
        use_fabric = info.get("use_fabric", False)

    # Use Fabric's main class if enabled
    if use_fabric and info.get("fabric_loader_path"):
        main_class = info.get("fabric_main_class", "net.fabricmc.loader.launch.knot.KnotClient")
        # Add Fabric loader and its libraries to classpath
        fabric_libs = info.get("fabric_lib_paths", [])
        classpath = classpath + fabric_libs + [info["fabric_loader_path"]]
    else:
        main_class = version_json.get("mainClass")

    if not main_class:
        raise ValueError("Missing mainClass")

    # asset index is usually inside version json
    asset_index = version_json.get("assetIndex", {}).get("id", version)

    # Determine auth values first (needed for both old and new argument formats)
    is_microsoft = auth or (minecraft_access_token and profile)

    if is_microsoft:
        should_reauth = False

        if auth:
            if config.get("do_not_share_-_microsoft_account_refresh_token"):
                try:
                    result = auth.refresh_and_get_minecraft(config["do_not_share_-_microsoft_account_refresh_token"])
                    config["do_not_share_-_microsoft_account_refresh_token"] = result["refresh_token"]
                    save_config()
                    minecraft_access_token = result["minecraft_access_token"]
                    profile = result["profile"]
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 400:
                        print("[!] Microsoft refresh token expired. Re-authenticating...")
                        should_reauth = True
            else:
                should_reauth = True

            if should_reauth:
                minecraft_access_token, profile = auth_microsoft(auth) # type: ignore
                minecraft_access_token = minecraft_access_token
                profile = profile
                if not minecraft_access_token or not profile:
                    print("Authentication failed. To not use Microsoft change use_microsoft_account in config.properties to False.")
                    return

        assert profile is not None
        assert minecraft_access_token is not None

        print(f"Hello {profile['name']}! You logged in with Microsoft.")
        auth_username = profile["name"]
        auth_uuid = profile["id"]
        auth_access_token = minecraft_access_token
    else:
        auth_username = username
        auth_uuid = "00000000-0000-0000-0000-000000000000"
        auth_access_token = "0"

    # Check if version supports new argument format
    has_arguments = "arguments" in version_json

    # Get memory settings from config (with defaults)
    max_memory = config.get("max_memory", "2G")
    min_memory = config.get("min_memory", "1G")

    # Get logging config path from info
    logging_config_path = info.get("logging_config_path")

    # Parse JVM arguments from Mojang or use fallback
    if has_arguments:
        jvm_args = parse_jvm_arguments(
            version_json,
            natives_dir,
            classpath,
            game_dir,
            assets_dir,
            asset_index,
            max_memory=max_memory,
            min_memory=min_memory,
            logging_config_path=logging_config_path
        )
    else:
        # Fallback for older versions without arguments field
        sep = ";" if platform.system().lower() == "windows" else ":"
        cp = sep.join(classpath)
        jvm_args = [
            f"-Djava.library.path={natives_dir}",
            f"-Xmx{max_memory}",
            f"-Xms{min_memory}",
            "-cp",
            cp,
        ]

    # Build the command
    cmd = [info["java_path"]] + jvm_args + [main_class]

    # Parse game arguments if available, otherwise use legacy format
    if has_arguments:
        game_args = parse_game_arguments(
            version_json,
            version,
            game_dir,
            assets_dir,
            asset_index,
            auth_username,
            auth_uuid,
            auth_access_token,
            client_id,
            xuid,
            version_type=version_json.get("type", "release")
        )
        cmd.extend(game_args)
    else:
        # Legacy format for older versions
        cmd.extend([
            "--version", version,
            "--gameDir", game_dir,
            "--assetsDir", assets_dir,
            "--assetIndex", asset_index,
            "--uuid", auth_uuid,
            "--accessToken", auth_access_token,
            "--username", auth_username
        ])

    print("\nLaunching Minecraft...\n")

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print("Minecraft crashed!")
        print(f"Exit code: {e.returncode}")


# ---------------- GUI ----------------

def exit_gui(app: ctk.CTk):
    config["geometry"] = f"{app.winfo_x()}+{app.winfo_y()}"
    save_config()
    app.destroy()

def start_gui():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    def ui(fn):
        app.after(0, fn)

    app = ctk.CTk()
    app.title("ElliNet13 Launcher")
    app.geometry(f"800x700+{config.get('geometry', '100+100')}")
    app.protocol("WM_DELETE_WINDOW", lambda: exit_gui(app))
    app.resizable(False, False)

    compressedLastVersionsList = config.get("last_versions_list", None)
    if compressedLastVersionsList:
        lastVersionsList = load_list(compressedLastVersionsList)
    else:
        lastVersionsList = [grab_minecraft_manifest()["latest"]["release"]]
        config["last_versions_list"] = save_list(lastVersionsList)

    version = ctk.StringVar()
    gameDir = ctk.StringVar()

    maincontainer = ctk.CTkFrame(app, fg_color="transparent")
    maincontainer.place(relx=0.5, rely=0.5, anchor="center")

    mainLabel = ctk.CTkLabel(maincontainer, text="Version")
    mainLabel.pack(pady=5)

    container = ctk.CTkFrame(maincontainer, fg_color="transparent")
    container.pack(expand=True)

    # ---------------- VERSION INPUT ----------------
    versionEntry = ctk.CTkEntry(container, textvariable=version)

    versionEntry.pack()

    # ---------------- GAME DIRECTORY INPUT ----------------
    gameDirLabel = ctk.CTkLabel(container, text="Game Data Directory")
    gameDirLabel.pack(pady=5)

    gameDirEntry = ctk.CTkEntry(container, textvariable=gameDir)
    gameDirEntry.pack()

    gameDirEntry.insert(0, config["dot_minecraft_dir"])

    # ---------------- FABRIC TOGGLE ----------------
    fabric_enabled = ctk.BooleanVar(value=config.get("use_fabric", "false").lower() == "true")

    def toggle_fabric():
        config["use_fabric"] = str(fabric_enabled.get()).lower()
        save_config()

    fabric_checkbox = ctk.CTkCheckBox(
        container,
        text="Enable Fabric",
        variable=fabric_enabled,
        command=toggle_fabric
    )
    fabric_checkbox.pack(pady=(10, 5))

    # ---------------- OMNIARCHIVE TOGGLE ----------------
    omniarchive_enabled = ctk.BooleanVar(value=config.get("use_omniarchive", "false").lower() == "true")

    def toggle_omniarchive():
        config["use_omniarchive"] = str(omniarchive_enabled.get()).lower()
        save_config()
        if omniarchive_enabled.get():
            omniarchive_browser_button.pack(pady=5, after=omniarchive_checkbox)
        else:
            omniarchive_browser_button.pack_forget()
        

    omniarchive_checkbox = ctk.CTkCheckBox(
        container,
        text="Enable Omniarchive",
        variable=omniarchive_enabled,
        command=toggle_omniarchive
    )
    omniarchive_checkbox.pack(pady=(10, 5))

    # ---------------- OMNIARCHIVE BROWSER BUTTON ----------------
    def open_omniarchive_browser():
        browser_app = ctk.CTkToplevel(app)
        browser_app.title("Omniarchive Browser")

        # Make sure window sizes are up to date
        app.update_idletasks()

        # Get parent window position and size
        parent_x = app.winfo_x()
        parent_y = app.winfo_y()
        parent_w = app.winfo_width()
        parent_h = app.winfo_height()

        # Calculate center position
        width = 600
        height = 500
        x = parent_x + (parent_w // 2) - (width // 2)
        y = parent_y + (parent_h // 2) - (height // 2)

        browser_app.geometry(f"{width}x{height}+{x}+{y}")
        browser_app.transient(app)
        browser_app.lift()

        browser_container = ctk.CTkFrame(browser_app, fg_color="transparent")
        browser_container.pack(expand=True, fill="both", padx=10, pady=10)

        # Title
        ctk.CTkLabel(browser_container, text="Omniarchive Versions", font=("", 16)).pack(pady=10)

        # Scrape button
        def scrape_and_refresh():
            scrape_omniarchive_index()
            refresh_eras()

        scrape_button = ctk.CTkButton(
            browser_container,
            text="Refresh Index (Scrape)",
            command=lambda: threading.Thread(target=scrape_and_refresh, daemon=True).start()
        )
        scrape_button.pack(pady=5)

        # Create a horizontal paned container for eras and versions
        paned_container = ctk.CTkFrame(browser_container)
        paned_container.pack(pady=5, fill="both", expand=True)

        # Left side - Era list
        eras_frame = ctk.CTkFrame(paned_container)
        eras_frame.pack(side="left", fill="both", expand=True, padx=(0, 5))

        ctk.CTkLabel(eras_frame, text="Eras").pack(pady=5)

        eras_scroll = ctk.CTkScrollableFrame(eras_frame, width=200, height=300)
        eras_scroll.pack(fill="both", expand=True, padx=5)

        # Right side - Version list
        versions_frame = ctk.CTkFrame(paned_container)
        versions_frame.pack(side="right", fill="both", expand=True, padx=(5, 0))

        ctk.CTkLabel(versions_frame, text="Versions").pack(pady=5)

        versions_scroll = ctk.CTkScrollableFrame(versions_frame, width=350, height=300)
        versions_scroll.pack(fill="both", expand=True, padx=5)

        def refresh_eras():
            # Clear existing buttons
            for widget in eras_scroll.winfo_children():
                widget.destroy()

            index = load_omniarchive_index()

            if not index:
                ctk.CTkLabel(eras_scroll, text="No index found. Click 'Refresh Index' to scrape.").pack(pady=20)
                return

            # Get eras in order
            eras = list(index.keys())

            for era in eras:
                era_btn = ctk.CTkButton(
                    eras_scroll,
                    text=era,
                    height=28,
                    command=lambda e=era: select_era(e)
                )
                era_btn.pack(fill="x", pady=2, padx=5)

            # Select first era by default if any exist
            if eras:
                select_era(eras[0])

        def select_era(era_name):
            # Clear existing buttons in versions
            for widget in versions_scroll.winfo_children():
                widget.destroy()

            index = load_omniarchive_index()
            if not index or era_name not in index:
                return

            era_versions = index[era_name]

            # Sort versions for this era
            def version_sort_key(v):
                parts = v.replace("-snapshot", "").replace("-pre", "").replace("-rc", "").replace("-beta", "").replace("-alpha", "").split(".")
                try:
                    return [int(p) if p.isdigit() else 0 for p in parts] + [0, 0]
                except:
                    return [0, 0, 0]

            sorted_versions = sorted(era_versions.keys(), key=version_sort_key, reverse=True)

            for v in sorted_versions:
                btn = ctk.CTkButton(
                    versions_scroll,
                    text=v,
                    height=28,
                    command=lambda val=v: (version.set(val), browser_app.destroy())
                )
                btn.pack(fill="x", pady=2, padx=5)

        refresh_eras()

        # Close button
        ctk.CTkButton(browser_container, text="Close", command=browser_app.destroy).pack(pady=10)

    omniarchive_browser_button = ctk.CTkButton(
        container,
        text="Browse Omniarchive",
        command=open_omniarchive_browser
    )
    if omniarchive_enabled.get():
        omniarchive_browser_button.pack(pady=5, after=omniarchive_checkbox)

    # ---------------- LAST VERSIONS LIST ----------------
    historyLabel = ctk.CTkLabel(container, text="Last Versions")
    historyLabel.pack(pady=(10, 5))

    scrollFrame = ctk.CTkScrollableFrame(container, width=250, height=200)
    scrollFrame.pack(pady=5)

    def set_version(v):
        version.set(v)

    # show newest first (optional)
    for v in lastVersionsList:
        btn = ctk.CTkButton(
            scrollFrame,
            text=v,
            height=28,
            command=lambda val=v: set_version(val)
        )
        btn.pack(fill="x", pady=2, padx=5)

    # ---------------- PLAY BUTTON ----------------
    playButton = ctk.CTkButton(
        container,
        text="Play",
        command=lambda: threading.Thread(
            target=launch_from_gui,
            args=(version, fabric_enabled, omniarchive_enabled, app, maincontainer, mainLabel, gameDir, container),
            daemon=True
        ).start()
    )
    playButton.pack(pady=20)

    latestButton = ctk.CTkButton(
        container,
        text="Fill in latest release",
        command=lambda: threading.Thread(
            target=lambda: set_version(grab_minecraft_manifest()["latest"]["release"]),
            daemon=True
        ).start()
    )
    latestButton.pack(pady=5)

    latestSnapshotButton = ctk.CTkButton(
        container,
        text="Fill in latest snapshot",
        command=lambda: threading.Thread(
            target=lambda: set_version(grab_minecraft_manifest()["latest"]["snapshot"]),
            daemon=True
        ).start()
    )
    latestSnapshotButton.pack(pady=5)

    app.mainloop()

def launch_from_gui(versionString: ctk.StringVar, fabricEnabled: ctk.BooleanVar, omniarchiveEnabled: ctk.BooleanVar, app: ctk.CTk, container: ctk.CTkFrame, mainLabel: ctk.CTkLabel, gameDir: ctk.StringVar, dcontainer: ctk.CTkFrame):
    def ui(fn):
        app.after(0, fn)

    try:
        version = versionString.get()
        use_fabric = fabricEnabled.get()
        use_omniarchive = omniarchiveEnabled.get()
        game_directory = gameDir.get()

        ui(lambda: dcontainer.destroy())
        ui(lambda: mainLabel.configure(text=f"Loading data for Minecraft version {version}..."))

        config["dot_minecraft_dir"] = game_directory
        save_config()

        # If omniarchive is enabled but index doesn't exist, scrape it
        if use_omniarchive and not load_omniarchive_index():
            ui(lambda: mainLabel.configure(text="Scraping Omniarchive index..."))
            scrape_omniarchive_index()

        if not verify_version(version, use_omniarchive=use_omniarchive):
            raise UserError(f"Version {version} not found.")

        versions = load_list(config.get("last_versions_list", "")) or []

        # remove duplicates of current version
        versions = [v for v in versions if v != version]

        # add to top
        versions.insert(0, version)

        config["last_versions_list"] = save_list(versions)

        ui(lambda: mainLabel.configure(text=f"Downloading Minecraft version {version}..."))
        info = download_game(version, use_fabric=use_fabric, use_omniarchive=use_omniarchive)

        ui(lambda: mainLabel.configure(text="Checking Microsoft authentication..."))

        if config["use_microsoft_account"].lower() == "true":
            auth = MCMSA()

            should_reauth = False

            if config.get("do_not_share_-_microsoft_account_refresh_token"):
                try:
                    result = auth.refresh_and_get_minecraft(
                        config["do_not_share_-_microsoft_account_refresh_token"]
                    )
                    config["do_not_share_-_microsoft_account_refresh_token"] = result["refresh_token"]
                    save_config()

                    minecraft_access_token = result["minecraft_access_token"]
                    profile = result["profile"]

                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 400:
                        should_reauth = True
            else:
                should_reauth = True

            if should_reauth:
                future = Future()
                ui(lambda: auth_microsoft_gui(auth, app, future))
                result = future.result()
                minecraft_access_token = result[0]
                profile = result[1]

            ui(lambda: mainLabel.configure(text=f"Starting Minecraft version {version}... (Microsoft account)"))
            time.sleep(1)
            ui(lambda: app.withdraw())
            start_minecraft(info, version=version, minecraft_access_token=minecraft_access_token, profile=profile, use_fabric=use_fabric, game_dir=game_directory)
            ui(lambda: exit_gui(app))
            return

        ui(lambda: mainLabel.configure(text=f"Starting Minecraft version {version}... (Offline account)"))
        time.sleep(1)
        ui(lambda: app.withdraw())
        start_minecraft(info, version=version, username=config["username"], use_fabric=use_fabric, game_dir=game_directory)
        ui(lambda: exit_gui(app))
    except UserError as e:
            ui(lambda: mainLabel.configure(text=str(e)))
            ui(lambda: ctk.CTkButton(container, text="Exit", command=lambda: exit_gui(app)).pack(pady=20))

def copy_to_clipboard(text: str, app: ctk.CTk):
    app.clipboard_clear()
    app.clipboard_append(text)

def auth_microsoft_gui(auth: MCMSA, original_app: ctk.CTk, future: Future):
    app = ctk.CTkToplevel(original_app)
    app.title("Login with Microsoft")

    width = 300
    height = 300

    # Make sure window sizes are up to date
    original_app.update_idletasks()

    # Get parent window position and size
    parent_x = original_app.winfo_x()
    parent_y = original_app.winfo_y()
    parent_w = original_app.winfo_width()
    parent_h = original_app.winfo_height()

    # Calculate center position
    x = parent_x + (parent_w // 2) - (width // 2)
    y = parent_y + (parent_h // 2) - (height // 2)

    app.geometry(f"{width}x{height}+{x}+{y}")
    
    app.transient(original_app)
    app.lift()

    device_info = auth.start_auth()

    container = ctk.CTkFrame(app, fg_color="transparent")
    container.pack(expand=True)
    ctk.CTkLabel(container, text="1. Open your browser and go to:").pack(pady=5)
    ctk.CTkLabel(container, text=device_info["verification_uri"]).pack(pady=5)
    ctk.CTkLabel(container, text="2. Enter the code:").pack(pady=5)
    ctk.CTkLabel(container, text=device_info["user_code"]).pack(pady=5)
    ctk.CTkLabel(container, text=f"3. The code expires in {device_info['expires_in']//60} minutes").pack(pady=5)

    ctk.CTkButton(container, text="Open browser", command=lambda: webbrowser.open(device_info["verification_uri"])).pack(pady=5)
    ctk.CTkButton(container, text="Copy code", command=lambda: copy_to_clipboard(device_info["user_code"], original_app)).pack(pady=5)
    ctk.CTkButton(container, text="I have completed the verification in browser", command=lambda: continue_auth()).pack(pady=5)

    def continue_auth():
        container.destroy()
        try:
            result = auth.finish_auth(device_info)

            minecraft_access_token = result["tokens"]["minecraft_access_token"]
            minecraft_refresh_token = result["tokens"]["microsoft_refresh_token"]

            config["do_not_share_-_microsoft_account_refresh_token"] = minecraft_refresh_token
            save_config()

            app.destroy()

            future.set_result((minecraft_access_token, result["profile"]))
        except Exception as e:
            print(f"Authentication failed: {e}")
            ctk.CTkLabel(container, text=f"Authentication failed: {e}").pack(pady=20)
            ctk.CTkButton(container, text="Exit", command=lambda: exit_gui(original_app)).pack(pady=20)


# ---------------- MAIN ----------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--version", type=str, required=False)
    parser.add_argument("-w", "--workers", type=int, default=30)
    parser.add_argument("--dont-run", action="store_false", dest="run")
    parser.add_argument("--fabric", action="store_true", dest="use_fabric")
    parser.add_argument("--no-fabric", action="store_false", dest="use_fabric")
    parser.add_argument("--fabric-set", type=str, dest="fabric_state", choices=["true", "false"], help="Set and save fabric state in config")
    parser.add_argument("--data-dir", type=str, dest="data_dir", help="Set .minecraft directory")
    parser.add_argument("--omniarchive", action="store_true", dest="use_omniarchive")
    parser.add_argument("--no-omniarchive", action="store_false", dest="use_omniarchive")
    parser.add_argument("--omniarchive-set", type=str, dest="omniarchive_state", choices=["true", "false"], help="Set and save omniarchive state in config")
    parser.add_argument("--scrape-omniarchive", action="store_true", help="Scrape the Omniarchive index")

    args = parser.parse_args()

    dot_minecraft_dir: str = args.data_dir if args.data_dir else config["dot_minecraft_dir"]

    # Handle --fabric-set to save preference in config
    if args.fabric_state:
        config["use_fabric"] = args.fabric_state
        save_config()
        print(f"Fabric preference saved: use_fabric={args.fabric_state}")

    # Handle --omniarchive-set to save preference in config
    if args.omniarchive_state:
        config["use_omniarchive"] = args.omniarchive_state
        save_config()
        print(f"Omniarchive preference saved: use_omniarchive={args.omniarchive_state}")

    # Handle --scrape-omniarchive
    if args.scrape_omniarchive:
        scrape_omniarchive_index()
        sys.exit(0)

    try:
        if args.version is None:
            start_gui()
            sys.exit(0)

        # Determine omniarchive usage: CLI arg takes precedence, otherwise use config
        use_omniarchive = args.use_omniarchive if args.use_omniarchive is not None else (config.get("use_omniarchive", "false").lower() == "true")

        # If omniarchive is enabled but index doesn't exist, scrape it
        if use_omniarchive and not load_omniarchive_index():
            print("Omniarchive index not found. Scraping...")
            scrape_omniarchive_index()

        # Determine fabric usage: CLI arg takes precedence, otherwise use config
        use_fabric = args.use_fabric if args.use_fabric is not None else (config.get("use_fabric", "false").lower() == "true")

        if not verify_version(args.version, use_omniarchive=use_omniarchive):
            raise ValueError(f"Minecraft version {args.version} not found.")

        info = download_game(args.version, workers=args.workers, use_fabric=use_fabric, use_omniarchive=use_omniarchive)

        if args.run:
            if config["use_microsoft_account"].lower() == "true":
                mcmsa = MCMSA()
            else:
                mcmsa = None
            start_minecraft(info, username=config["username"], version=args.version, auth=mcmsa, use_fabric=use_fabric, game_dir=dot_minecraft_dir)
    except KeyboardInterrupt:
        print("\nInterrupted by user. Stopping...")
        sys.exit(1)
    except KeyError:
        exc_type, exc_value, exc_tb = sys.exc_info()
        tb = traceback.extract_tb(exc_tb)

        if any("propclass.py" in frame.filename for frame in tb):
            print(f"Error! Invalid config file. Missing: {exc_value}")
        else:
            raise
