
import json
import requests
import re
import time

OUTPUT_FILE = "sentinel.json"

# --- DATA SOURCES ---

# 1. ThreatFox (Abuse.ch) - High Confidence Recent IOCs
THREATFOX_URLS = ["https://threatfox.abuse.ch/export/csv/recent/"]

# 2. MalwareBazaar (Abuse.ch) - Recent Verified Malware
MALWARE_BAZAAR_URLS = ["https://bazaar.abuse.ch/export/txt/sha256/recent/"]

# 3. Malware Hash Database (Aaryan Londhe) - Massive Historical Archive
# We iterate through files 1 to 6.
AARYAN_BASE_URL = "https://raw.githubusercontent.com/aaryanrlondhe/Malware-Hash-Database/main/SHA256/sha256_hashes_{}.txt"

# Regex for SHA256 (64 hex chars)
HASH_PATTERN = re.compile(r'\b[a-fA-F0-9]{64}\b')

# Browser Headers
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/plain,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

def get_hashes(text):
    return set(HASH_PATTERN.findall(text))

def fetch_simple_source(name, urls):
    print(f"   🔎 Fetching {name}...")
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=45)
            if r.status_code == 200:
                hashes = get_hashes(r.text)
                print(f"      ✅ {name}: {len(hashes)} signatures.")
                return hashes
            else:
                print(f"      ⚠️ {name} Error ({r.status_code})")
        except Exception as e:
            print(f"      ❌ {name} Exception: {str(e)[:50]}")
    return set()

def fetch_archive_source():
    print(f"   🔎 Fetching Malware Hash Archive (1-6)...")
    all_hashes = set()
    
    # Loop through files 1 to 6
    for i in range(1, 7):
        url = AARYAN_BASE_URL.format(i)
        try:
            print(f"      ...Downloading Part {i}")
            r = requests.get(url, headers=HEADERS, timeout=60)
            if r.status_code == 200:
                # Optimized line-by-line processing for large files
                count = 0
                for line in r.iter_lines(decode_unicode=True):
                    if line:
                        clean = line.strip().lower()
                        # Quick validation length check before regex
                        if len(clean) == 64:
                            all_hashes.add(clean)
                            count += 1
                print(f"      ✅ Part {i}: {count} signatures added.")
            else:
                print(f"      ⚠️ Part {i} Missing ({r.status_code})")
        except Exception as e:
            print(f"      ❌ Part {i} Failed: {str(e)[:50]}")
            
    return all_hashes

def run():
    print("🛡️ Orion Sentinel Compiler")
    
    final_list = []
    unique_hashes = set()

    # 1. Fetch Live Feeds
    tf_hashes = fetch_simple_source("ThreatFox", THREATFOX_URLS)
    mb_hashes = fetch_simple_source("MalwareBazaar", MALWARE_BAZAAR_URLS)
    
    # 2. Fetch Deep Archive
    archive_hashes = fetch_archive_source()

    # 3. Compile
    # Priority: ThreatFox > Bazaar > Archive
    all_sets = [
        (tf_hashes, "Recent Threat (ThreatFox)"),
        (mb_hashes, "Confirmed Malware (Bazaar)"),
        (archive_hashes, "Known Virus (Archive)")
    ]

    print("\n   ⚙️  Compiling Database...")
    for hash_set, label in all_sets:
        for h in hash_set:
            if h not in unique_hashes:
                # Optimization: For the massive archive, we just store the hash.
                # The label is applied generically during scan to save JSON size.
                entry = {"hash": h.lower()}
                # Only add name if it's a specific high-priority source to save space
                if "Archive" not in label:
                    entry["name"] = label
                
                final_list.append(entry)
                unique_hashes.add(h)

    # Manual Keys (Test Viruses)
    manual = [
        ("275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f", "EICAR-Test-Signature"),
        ("5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8", "Orion-Test-Virus"),
    ]
    for h, n in manual:
        if h not in unique_hashes:
            final_list.append({"hash": h, "name": n})
            unique_hashes.add(h)

    # 4. Sort (Crucial for GZIP Compression efficiency)
    print("   ✨ Sorting Hashes...")
    final_list.sort(key=lambda x: x['hash'])

    print(f"\n📦 Total Unique Signatures: {len(final_list)}")
    
    # Save compacted JSON
    with open(OUTPUT_FILE, "w") as f:
        json.dump(final_list, f, separators=(',', ':'))

if __name__ == "__main__":
    run()
