
import json
import requests
import hashlib

# Sources
SOURCES = [
    # MalwareBazaar (Recent Android Malware - SHA256)
    {"url": "https://bazaar.abuse.ch/export/txt/sha256/recent/", "type": "text", "filter": "android"},
    # URLHaus (Malicious URLs, usually not file hashes but good to have if we expand)
    # Keeping it simple: Only file hashes for now.
]

OUTPUT_FILE = "sentinel.json"

def fetch_and_parse():
    threats = set()
    
    print("🛡️ Orion Sentinel Compiler")
    
    # 1. Fetch MalwareBazaar
    try:
        print("⬇️ Fetching MalwareBazaar...")
        r = requests.post("https://bazaar.abuse.ch/api/1/", data={"query": "get_recent", "selector": "100"}) # Limit for demo
        if r.status_code == 200:
            data = r.json()
            if data.get("query_status") == "ok":
                for sample in data.get("data", []):
                    # Filter for Android
                    tags = sample.get("tags", [])
                    file_type = sample.get("file_type", "")
                    if "apk" in file_type or "android" in tags or "apk" in tags:
                        threats.add((sample["sha256_hash"], sample.get("signature", "Unknown"), "MalwareBazaar"))
    except Exception as e:
        print(f"❌ Error fetching MalwareBazaar: {e}")

    # 2. Compile List
    final_list = []
    for h, name, src in threats:
        final_list.append({
            "hash": h,
            "name": name,
            "source": src
        })
    
    print(f"✅ Compiled {len(final_list)} signatures.")
    
    with open(OUTPUT_FILE, "w") as f:
        json.dump(final_list, f)

if __name__ == "__main__":
    fetch_and_parse()
