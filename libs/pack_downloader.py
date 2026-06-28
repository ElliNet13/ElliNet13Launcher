import requests
import os
import signal
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from pathlib import Path

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