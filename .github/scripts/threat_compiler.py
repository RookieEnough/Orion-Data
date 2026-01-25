
import json
import requests
import hashlib

OUTPUT_FILE = "sentinel.json"

def fetch_and_parse():
    threats = set()
    
    print("🛡️ Orion Sentinel Compiler")
    print("   Targeting: Android/APK Signatures")
    
    # 1. Fetch by TAG: "android" (Specific Android Malware)
    try:
        print("⬇️ Fetching MalwareBazaar (Tag: Android)...")
        r = requests.post("https://bazaar.abuse.ch/api/1/", data={"query": "get_taginfo", "tag": "android", "limit": "1000"})
        if r.status_code == 200:
            data = r.json()
            if data.get("query_status") == "ok":
                for sample in data.get("data", []):
                    sig = sample.get("signature") or "Android.Malware.Generic"
                    if sample.get("sha256_hash"):
                        threats.add((sample["sha256_hash"], sig, "MalwareBazaar"))
            else:
                print(f"   ⚠️ API Query Status: {data.get('query_status')}")
        else:
            print(f"   ⚠️ HTTP Error: {r.status_code}")
    except Exception as e:
        print(f"❌ Error fetching Android Tag: {e}")

    # 2. Fetch by FILE TYPE: "apk" (Broad Search)
    try:
        print("⬇️ Fetching MalwareBazaar (Type: APK)...")
        r = requests.post("https://bazaar.abuse.ch/api/1/", data={"query": "get_file_type", "file_type": "apk", "limit": "1000"})
        if r.status_code == 200:
            data = r.json()
            if data.get("query_status") == "ok":
                for sample in data.get("data", []):
                    sig = sample.get("signature") or "Android.Malware.Generic"
                    if sample.get("sha256_hash"):
                        threats.add((sample["sha256_hash"], sig, "MalwareBazaar"))
    except Exception as e:
        print(f"❌ Error fetching APK Type: {e}")

    # 3. Add Manual Test Signatures (Safe for testing)
    # EICAR Test File (Standard AV Test)
    threats.add(("275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f", "EICAR-Test-Signature", "Manual"))
    # A common test hash for Android malware debug
    threats.add(("5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8", "Orion-Test-Virus", "Manual"))

    # Compile List
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
