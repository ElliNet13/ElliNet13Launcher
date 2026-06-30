import requests
import os
import signal
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from pathlib import Path
import platform
import lzma
import io

ASSETS_BASE = "https://resources.download.minecraft.net/"
STOP_EVENT = threading.Event()

def handle_sigint(signum, frame):
    if not STOP_EVENT.is_set():
        print("\nInterrupted by user. Stopping...")
        STOP_EVENT.set()

def is_valid_file(path, expected_size=None):
    if not path.exists():
        return False
    if expected_size is not None:
        return path.stat().st_size == expected_size
    return path.stat().st_size > 0

signal.signal(signal.SIGINT, handle_sigint)

session = requests.Session()

def grab_minecraft_manifest():
    url = "https://launchermeta.mojang.com/mc/game/version_manifest.json"
    r = session.get(url)
    r.raise_for_status()
    return r.json()

def grab_version_json(version_info):
    r = session.get(version_info["url"])
    r.raise_for_status()
    return r.json()

def detect_arch():
    machine = platform.machine().lower()

    # Normalize common Python outputs
    if machine in ("amd64", "x86_64", "x64"):
        return "x64"
    if machine in ("i386", "i686", "x86"):
        return "x86"
    if "arm64" in machine or "aarch64" in machine:
        return "arm64"

    raise Exception(f"Unknown architecture: {machine}")

def grab_java_runtime_manifest():
    url = "https://launchermeta.mojang.com/v1/products/java-runtime/2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json"
    r = session.get(url)
    r.raise_for_status()
    return r.json()

def grab_java_runtime_version_manifest(version_info: dict) -> dict:
    runtime_index = grab_java_runtime_manifest()

    component = version_info["javaVersion"]["component"]

    system = platform.system().lower()
    arch = detect_arch()

    # Build exact OS keys (NO guessing fallback selection)
    if system == "windows":
        os_key = f"windows-{arch}"
        if arch not in ["x64", "x86", "arm64"]:
            raise Exception("Unsupported Windows arch")
        os_candidates = [os_key]

    elif system == "linux":
        # Linux is special: x64 uses "linux", 32-bit uses "linux-i386"
        if arch == "x86":
            os_candidates = ["linux-i386"]
        else:
            os_candidates = ["linux"]

    elif system == "darwin":
        if arch == "arm64":
            os_candidates = ["mac-os-arm64"]
        else:
            os_candidates = ["mac-os"]

    else:
        raise Exception(f"Unsupported OS: {system}")

    # Resolve runtime
    for os_key in os_candidates:
        os_bucket = runtime_index.get(os_key)
        if not os_bucket:
            continue

        runtime_list = os_bucket.get(component)
        if not runtime_list:
            continue

        # pick latest entry (Mojang orders newest first usually)
        runtime_entry = runtime_list[0]

        java_runtime_manifest_url = runtime_entry["manifest"]["url"]
        java_runtime_manifest = session.get(java_runtime_manifest_url)
        java_runtime_manifest.raise_for_status()

        return java_runtime_manifest.json()

    raise Exception(
        f"No runtime found for component={component}, os={system}, arch={arch}"
    )

def download_java_runtime(version_info: dict, folder: Path):
    runtime_manifest = grab_java_runtime_version_manifest(version_info)
    files = runtime_manifest["files"]

    # Create required folders
    folder.mkdir(parents=True, exist_ok=True)

    for name, info in tqdm(files.items(), total=len(files), desc="Downloading Java Runtime"):
        name: str
        info: dict
        match info.get("type"):
            case "directory":
                Path(folder / name).mkdir(parents=True, exist_ok=True)

            case "file":
                path = folder / name
                if info["downloads"].get("lzma"):
                    url = info["downloads"]["lzma"]["url"]

                    if not path.exists():
                        compressed_file = session.get(url)
                        compressed_file.raise_for_status()

                        with lzma.open(io.BytesIO(compressed_file.content), "rb") as f:
                            with open(path, "wb") as out:
                                out.write(f.read())
                else:
                    url = info["downloads"]["raw"]["url"]

                    if not path.exists():
                        download_file(url, path)

                if info["executable"]:
                    st = os.stat(path)
                    os.chmod(path, st.st_mode | 0o111)

def download_file(url, path):
    r = session.get(url, stream=True)
    r.raise_for_status()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)

def download_single_asset(args):
    key, value, output_dir = args

    hash_val = value["hash"]
    size = value["size"]

    subdir = hash_val[:2]
    url = f"{ASSETS_BASE}{subdir}/{hash_val}"

    dest = os.path.join(
        output_dir,
        "objects",
        hash_val[:2],
        hash_val
    )

    if is_valid_file(Path(dest), size):
        return True

    if STOP_EVENT.is_set():
        return False

    try:
        r = session.get(url, stream=True, timeout=15)
        r.raise_for_status()

        os.makedirs(os.path.dirname(dest), exist_ok=True)

        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                if STOP_EVENT.is_set():
                    return False
                f.write(chunk)
        return True

    except Exception as e:
        if STOP_EVENT.is_set():
            return False
        print(f"Failed: {key} -> {e}")
        return False

def download_assets(asset_index, output_dir="pack", workers=12):
    objects = asset_index["objects"]

    print(f"Downloading {len(objects)} assets with {workers} threads...")

    tasks = [(k, v, output_dir) for k, v in objects.items()]
    total = len(tasks)

    done = 0
    success = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(download_single_asset, t) for t in tasks]

        try:
            for f in tqdm(as_completed(futures), total=total, desc="Assets"):
                result = f.result()
                done += 1
                if result:
                    success += 1
        except KeyboardInterrupt:
            STOP_EVENT.set()
            print("\nInterrupted during asset download. Waiting for workers to stop...")
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)

    print(f"Assets done: {success}/{total}")