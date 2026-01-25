
import json
import requests
import time

OUTPUT_FILE = "sentinel.json"
API_URL = "https://mb-api.abuse.ch/api/v1/"

# Tags to search for ensuring we get Android-specific threats
TARGET_TAGS = ["android", "apk", "bankbot", "hiddad", "joker"]

def query_api(tag):
    print(f"   🔎 Querying API for tag: '{tag}'...")
    try:
        data = {
            "query": "get_taginfo",
            "tag": tag,
            "limit": 1000  # Max limit per query
        }
        r = requests.post(API_URL, data=data, timeout=30)
        if r.status_code == 200:
            json_data = r.json()
            if json_data.get("query_status") == "ok":
                return json_data.get("data", [])
            else:
                print(f"      ⚠️ API Status: {json_data.get('query_status')}")
        else:
            print(f"      ❌ HTTP Error: {r.status_code}")
    except Exception as e:
        print(f"      ❌ Connection failed: {e}")
    return []

def run_compiler():
    print("🛡️ Orion Sentinel Compiler (API Mode)")
    print("   Source: MalwareBazaar API")
    
    unique_threats = {} # Map hash -> data to prevent duplicates

    # 1. Fetch from API
    for tag in TARGET_TAGS:
        results = query_api(tag)
        print(f"      ↳ Found {len(results)} entries.")
        
        for entry in results:
            sha256 = entry.get("sha256_hash")
            file_type = entry.get("file_type")
            signature = entry.get("signature") or "Android.Trojan.Generic"
            
            # Double check it is actually an Android file
            if sha256 and (file_type == "apk" or "android" in entry.get("tags", [])):
                unique_threats[sha256] = {
                    "hash": sha256,
                    "name": signature,
                    "source": "MalwareBazaar",
                    "type": "malware"
                }
        
        # Be nice to the API
        time.sleep(1)

    # 2. Add Manual Signatures (Tests)
    manual_threats = [
        ("275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f", "EICAR-Test-Signature"),
        ("5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8", "Orion-Test-Virus")
    ]
    
    for h, n in manual_threats:
        unique_threats[h] = {
            "hash": h,
            "name": n,
            "source": "Manual",
            "type": "test"
        }

    # 3. Export
    final_list = list(unique_threats.values())
    print(f"\n✅ Compiled {len(final_list)} unique Android signatures.")
    
    with open(OUTPUT_FILE, "w") as f:
        json.dump(final_list, f, indent=None)

if __name__ == "__main__":
    run_compiler()
