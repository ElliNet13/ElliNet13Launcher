import requests
from bs4 import BeautifulSoup
import json
import urllib.parse

def scrape_omniarchive():
    root_url = "https://omniarchive.uk/archive/java/client/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LauncherIndexer/1.0"
    }
    
    print(f"Connecting to: {root_url}")
    try:
        response = requests.get(root_url, headers=headers, timeout=15)
    except Exception as e:
        print(f"Network Error: {e}")
        return

    if response.status_code != 200:
        print(f"Failed to access root. Status: {response.status_code}")
        return

    soup = BeautifulSoup(response.text, 'html.parser')
    
    # 1. Capture the sub-era links IN THE EXACT ORDER they appear on the page
    ordered_era_urls = []
    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        href_str = "".join(href) if isinstance(href, list) else str(href)
        text = link.text.strip().lower()
        
        # Target the links that say exactly 'client'
        if text == "client" and "/archive/java/client/" in href_str:
            absolute_era_url = urllib.parse.urljoin(root_url, href_str)
            # Avoid list duplicates but keep the original DOM order intact
            if absolute_era_url not in ordered_era_urls:
                ordered_era_urls.append(absolute_era_url)

    print(f"Found {len(ordered_era_urls)} era sub-pages.")
    
    # Python dictionary preserves insertion order
    ordered_master_index = {}

    # 2. Walk through every era page in chronological sequence
    for era_url in ordered_era_urls:
        path_parts = [part for part in era_url.split('/') if part]
        era_key = path_parts[-1] if path_parts[-1] != "client" else path_parts[-2]
        
        print(f"Scanning era page: '{era_key}'")
        ordered_master_index[era_key] = {}

        try:
            era_res = requests.get(era_url, headers=headers, timeout=15)
            if era_res.status_code != 200:
                continue
        except Exception:
            continue

        era_soup = BeautifulSoup(era_res.text, 'html.parser')
        
        # 3. Read versions in the exact order they are arranged in the table
        for file_link in era_soup.find_all('a', href=True):
            href_val = file_link.get('href', '')
            download_url = "".join(href_val) if isinstance(href_val, list) else str(href_val)
            
            if download_url.endswith('.jar'):
                filename = download_url.split('/')[-1]
                is_launcher_jar = filename.endswith('-launcher.jar')
                
                clean_name = filename[:-4]  # Remove '.jar'
                if clean_name.endswith('-launcher'):
                    clean_name = clean_name[:-9]  # Remove '-launcher'

                if clean_name not in ordered_master_index[era_key] or is_launcher_jar:
                    ordered_master_index[era_key][clean_name] = download_url

    # Strip empty categories
    ordered_master_index = {k: v for k, v in ordered_master_index.items() if v}

    # Save out the structural order directly to the file
    output_filename = "omniarchive.json"
    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(ordered_master_index, f, indent=4, ensure_ascii=False)

    print(f"Saved Omniarchive index to: {output_filename}")

if __name__ == "__main__":
    scrape_omniarchive()