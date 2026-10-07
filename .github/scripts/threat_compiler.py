import json
import requests
import re
import os
import time

# --- DATA SOURCES ---
THREATFOX_URLS = [
    "https://threatfox.abuse.ch/export/csv/recent/",
    "https://threatfox.abuse.ch/export/csv/full/",
]
MALWARE_BAZAAR_URLS = [
    "https://bazaar.abuse.ch/export/txt/sha256/recent/",
    "https://bazaar.abuse.ch/export/txt/sha256/full/",
]

HASH_PATTERN = re.compile(r'\b[a-fA-F0-9]{64}\b')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/plain,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

def get_hashes_streaming(response):
    """Extract hashes from streaming response to avoid loading huge files into memory."""
    hashes = set()
    for chunk in response.iter_content(chunk_size=8192, decode_unicode=True):
        if chunk:
            hashes.update(HASH_PATTERN.findall(chunk))
    return hashes

def fetch_with_retry(url, max_retries=3):
    """Fetch URL with retries and streaming."""
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=180, stream=True)
            if r.status_code == 200:
                hashes = get_hashes_streaming(r)
                return hashes
            else:
                print(f"      ⚠️ HTTP {r.status_code}: {url} (attempt {attempt+1})")
        except Exception as e:
            print(f"      ❌ {str(e)[:60]} (attempt {attempt+1}): {url}")
        if attempt < max_retries - 1:
            wait = (attempt + 1) * 10
            print(f"      ⏳ Retrying in {wait}s...")
            time.sleep(wait)
    return set()

def fetch_simple_source(name, urls):
    print(f"   🔎 Fetching {name}...")
    all_hashes = set()
    for url in urls:
        hashes = fetch_with_retry(url)
        if hashes:
            print(f"      ✅ {name} ({url.split('/')[-2]}): {len(hashes)} signatures.")
            all_hashes |= hashes
        else:
            print(f"      ⚠️ {name} ({url.split('/')[-2]}): no data after retries, skipping.")
    print(f"      📊 {name} total unique: {len(all_hashes)}")
    return all_hashes

def run():
    print("🛡️ Orion Sentinel Compiler (v14.2 - Robust)")
    
    tf_hashes = fetch_simple_source("ThreatFox", THREATFOX_URLS)
    mb_hashes = fetch_simple_source("MalwareBazaar", MALWARE_BAZAAR_URLS)

    total_fetched = len(tf_hashes | mb_hashes)
    print(f"\n📊 Total unique hashes fetched: {total_fetched}")
    
    # Safety: don't wipe the database if we got almost nothing
    # Normal daily fetch should yield 100k+ hashes
    if total_fetched < 1000:
        print(f"❌ FATAL: Only {total_fetched} hashes fetched, aborting to preserve existing database.")
        print("   This is likely a transient feed issue. The workflow will retry tomorrow.")
        exit(1)

    # Compile into buckets
    print("\n   ⚙️  Sharding Database into 16 buckets...")
    buckets = {hex(i)[2:]: [] for i in range(16)}
    processed_hashes = set()

    def add_to_bucket(hash_set, label):
        for h in hash_set:
            if h not in processed_hashes:
                bucket_char = h[0].lower()
                if bucket_char not in buckets:
                    continue
                buckets[bucket_char].append({"h": h, "n": label})
                processed_hashes.add(h)

    add_to_bucket(tf_hashes, "ThreatFox")
    add_to_bucket(mb_hashes, "MalwareBazaar")

    manual = [
        ("275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f", "EICAR-Test"),
        ("5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8", "Orion-Test"),
    ]
    for h, n in manual:
        if h not in processed_hashes:
            bucket_char = h[0].lower()
            if bucket_char in buckets:
                buckets[bucket_char].append({"h": h, "n": n})
                processed_hashes.add(h)

    # Write shards
    if not os.path.exists("sentinel"):
        os.makedirs("sentinel")

    print("\n   💾 Saving Shards...")
    total_count = 0
    for char, data in buckets.items():
        data.sort(key=lambda x: x['h'])
        filename = f"sentinel/shard_{char}.json"
        with open(filename, "w") as f:
            json.dump(data, f, separators=(',', ':'))
        print(f"      📦 {filename}: {len(data)} entries")
        total_count += len(data)

    print(f"\n📦 Total Unique Signatures: {total_count}")
    print("✅ Compile complete.")

if __name__ == "__main__":
    run()
