
import json
import requests
import re
import os

# --- DATA SOURCES ---
# abuse.ch feeds: 'recent' is the last 7 days, 'full' is the entire database.
THREATFOX_URLS = [
    "https://threatfox.abuse.ch/export/csv/recent/",
    "https://threatfox.abuse.ch/export/csv/full/",
]
MALWARE_BAZAAR_URLS = [
    "https://bazaar.abuse.ch/export/txt/sha256/recent/",
    "https://bazaar.abuse.ch/export/txt/sha256/full/",
]
# AARYAN_BASE_URL removed 2026-10-04: repo deleted (404 on all parts)

# Regex for SHA256 (64 hex chars)
HASH_PATTERN = re.compile(r'\b[a-fA-F0-9]{64}\b')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/plain,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

def get_hashes(text):
    return set(HASH_PATTERN.findall(text))

def fetch_simple_source(name, urls):
    print(f"   🔎 Fetching {name}...")
    all_hashes = set()
    for url in urls:
        try:
            r = requests.get(url, headers=HEADERS, timeout=120)
            if r.status_code == 200:
                hashes = get_hashes(r.text)
                print(f"      ✅ {name} ({url.split('/')[-2]}): {len(hashes)} signatures.")
                all_hashes |= hashes
            else:
                print(f"      ⚠️ {name} Error ({r.status_code}): {url}")
        except Exception as e:
            print(f"      ❌ {name} Exception: {str(e)[:50]} ({url})")
    print(f"      📊 {name} total unique: {len(all_hashes)}")
    return all_hashes

def run():
    print("🛡️ Orion Sentinel Compiler (v14.1 - Atomic Sharding)")
    
    # 1. Fetch
    tf_hashes = fetch_simple_source("ThreatFox", THREATFOX_URLS)
    mb_hashes = fetch_simple_source("MalwareBazaar", MALWARE_BAZAAR_URLS)

    # 2. Compile into Buckets (0-9, a-f)
    print("\n   ⚙️  Sharding Database into 16 buckets...")
    
    # Initialize 16 buckets
    buckets = {hex(i)[2:]: [] for i in range(16)}
    
    processed_hashes = set()

    # Priority Helper
    def add_to_bucket(hash_set, label):
        for h in hash_set:
            if h not in processed_hashes:
                # Determine bucket char (first char of hash)
                bucket_char = h[0]
                
                entry = {"h": h} # Minimal key 'h' for hash
                entry["n"] = label # Minimal key 'n' for name
                
                buckets[bucket_char].append(entry)
                processed_hashes.add(h)

    # Process in Priority Order
    add_to_bucket(tf_hashes, "ThreatFox")
    add_to_bucket(mb_hashes, "MalwareBazaar")

    # Manual Keys
    manual = [
        ("275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f", "EICAR-Test"),
        ("5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8", "Orion-Test"),
    ]
    for h, n in manual:
        if h not in processed_hashes:
            bucket_char = h[0]
            buckets[bucket_char].append({"h": h, "n": n})
            processed_hashes.add(h)

    # 3. Write Shards
    total_count = 0
    if not os.path.exists("sentinel"):
        os.makedirs("sentinel")

    print("\n   💾 Saving Shards...")
    for char, data in buckets.items():
        # Sort for better GZIP compression downstream
        data.sort(key=lambda x: x['h'])
        
        filename = f"sentinel/shard_{char}.json"
        with open(filename, "w") as f:
            # Separators remove whitespace for smaller size
            json.dump(data, f, separators=(',', ':'))
        
        print(f"      📦 {filename}: {len(data)} entries")
        total_count += len(data)

    print(f"\n📦 Total Unique Signatures: {total_count}")

if __name__ == "__main__":
    run()
