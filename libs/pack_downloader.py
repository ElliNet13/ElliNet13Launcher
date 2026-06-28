import requests
import os
import signal
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

ASSETS_BASE = "https://resources.download.minecraft.net/"
STOP_EVENT = threading.Event()


def handle_sigint(signum, frame):
    if not STOP_EVENT.is_set():
        print("\nInterrupted by user. Stopping...")
        STOP_EVENT.set()


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

    hash = value["hash"]
    subdir = hash[:2]
    url = f"{ASSETS_BASE}{subdir}/{hash}"

    dest = os.path.join(output_dir, "assets", key.replace("/", os.sep))
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    if STOP_EVENT.is_set():
        return False

    try:
        r = session.get(url, timeout=15)
        r.raise_for_status()

        with open(dest, "wb") as f:
            f.write(r.content)

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