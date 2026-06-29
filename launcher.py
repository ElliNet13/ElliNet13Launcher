import argparse
from libs.pack_downloader import *

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
from tkinter import PhotoImage
import webbrowser
from concurrent.futures import Future
import time

# Load config
configfile = "config.properties"
if not os.path.exists("config.properties"):
    shutil.copyfile("config.template.properties", "config.properties")
with open(configfile, "r", encoding="latin-1") as f:
    config = PropertiesFile.load(f)

def save_config():
    with open(configfile, "w", encoding="latin-1") as f:
        config.dump(f)

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
    jar_url = version_json["downloads"]["client"]["url"]

    jar_path = version_dir / f"{version}.jar"

    if safe_download(session, jar_url, jar_path):
        print("Downloaded client jar")
    else:
        print("Client jar already exists, skipping")

    # ---------------- LIBRARIES ----------------
    print("Downloading libraries...")
    classpath = []

    for lib in tqdm(version_json["libraries"], desc="Libraries"):
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
def verify_version(version):
    manifest = grab_minecraft_manifest()

    versions = manifest.get("versions", [])

    for v in versions:
        if v.get("id") == version:
            return True

    return False

# ---------------- START MINECRAFT ----------------
def start_minecraft(info, version, username="Player", java_path="java", auth: MCMSA|None = None, profile:None|dict=None, minecraft_access_token:None|str=None): # type: ignore
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
        "--version", version,
        "--gameDir", game_dir,
        "--assetsDir", assets_dir,
        "--assetIndex", asset_index,
    ]

    if auth or (minecraft_access_token and profile):
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
        cmd.extend([
            "--userType", "msa",
            "--uuid", profile["id"],
            "--accessToken", minecraft_access_token,
            "--username", profile["name"]
        ])

    else:
        cmd.extend([
            "--userType", "legacy",
            "--uuid", "00000000-0000-0000-0000-000000000000",
            "--accessToken", "0",
            "--username", username
        ])

    print("\nLaunching Minecraft...\n")

    subprocess.run(cmd)


# ---------------- GUI ----------------

def exit_gui(app: ctk.CTk):
    config["geometry"] = f"{app.winfo_width()}x{app.winfo_height()}+{app.winfo_x()}+{app.winfo_y()}"
    save_config()
    app.destroy()

def start_gui():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    def ui(fn):
        app.after(0, fn)

    app = ctk.CTk()
    app.title("ElliNet13 Launcher")
    app.geometry(config.get("geometry", "800x600"))
    app.protocol("WM_DELETE_WINDOW", lambda: exit_gui(app))
    app.resizable(False, False)

    version = ctk.StringVar()

    container = ctk.CTkFrame(app, fg_color="transparent")
    container.pack(expand=True)

    mainLabel = ctk.CTkLabel(container, text="Version")
    versionEntry = ctk.CTkEntry(container, textvariable=version)
    mainLabel.pack(pady=5)
    versionEntry.pack()

    playButton = ctk.CTkButton(
        container,
        text="Play",
        command=lambda: threading.Thread(
            target=launch_from_gui,
            args=(version, app, container, mainLabel, versionEntry, playButton),
            daemon=True
        ).start()
    )
    playButton.pack(pady=20)

    app.mainloop()

def launch_from_gui(versionString: ctk.StringVar, app: ctk.CTk, container: ctk.CTkFrame, mainLabel: ctk.CTkLabel, versionEntry: ctk.CTkEntry, playButton: ctk.CTkButton):
    def ui(fn):
        app.after(0, fn)

    version = versionString.get()

    ui(lambda: playButton.destroy())
    ui(lambda: versionEntry.destroy())
    ui(lambda: mainLabel.configure(text=f"Loading data for Minecraft version {version}..."))

    if not verify_version(version):
        ui(lambda: mainLabel.configure(text=f"Version {version} not found."))
        ui(lambda: ctk.CTkButton(container, text="Exit", command=lambda: exit_gui(app)).pack(pady=20))
        return

    ui(lambda: mainLabel.configure(text=f"Downloading Minecraft version {version}..."))
    info = download_game(version)

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
        
        ui(lambda: mainLabel.configure(text=f"Starting Minecraft version {version}..."))
        time.sleep(1)
        ui(lambda: exit_gui(app))
        start_minecraft(info, version=version, java_path=config["java_path"], minecraft_access_token=minecraft_access_token, profile=profile)

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

    args = parser.parse_args()

    try:
        if args.version is None:
            start_gui()
            sys.exit(0)

        if not verify_version(args.version):
            raise ValueError(f"Minecraft version {args.version} not found.")
        
        info = download_game(args.version, workers=args.workers)

        if args.run:
            if config["use_microsoft_account"].lower() == "true":
                mcmsa = MCMSA()
            else:
                mcmsa = None
            start_minecraft(info, username=config["username"], java_path=config["java_path"], version=args.version, auth=mcmsa)
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
