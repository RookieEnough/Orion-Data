
import json
import requests
import os
import csv
import io

OUTPUT_FILE = "sentinel.json"

# --- DATA SOURCES ---

# 1. ThreatFox (Abuse.ch) - Recent IOCs
# High volume, frequent updates. We filter for Android tags.
THREATFOX_CSV_URL = "https://threatfox.abuse.ch/export/csv/recent/"

# 2. AssoEchap (GitHub) - Stalkerware Indicators
# Excellent for spyware. We try multiple branches in case of 404.
ECHAP_URLS = [
    "https://raw.githubusercontent.com/AssoEchap/stalkerware-indicators/master/generated/stalkerware.csv",
    "https://raw.githubusercontent.com/AssoEchap/stalkerware-indicators/main/generated/stalkerware.csv"
]

# 3. MalwareBazaar (Optional - API Key)
MB_API_URL = "https://mb-api.abuse.ch/api/v1/"
MB_TAGS = ["android", "apk", "bankbot", "hiddad", "joker"]

def fetch_threatfox_data():
    print("   🔎 Fetching ThreatFox Recent IOCs...")
    threats = []
    try:
        r = requests.get(THREATFOX_CSV_URL, timeout=30)
        if r.status_code == 200:
            # Skip comment lines that start with #
            lines = [line for line in r.text.splitlines() if not line.startswith('#')]
            reader = csv.reader(lines)
            
            # ThreatFox CSV structure:
            # 0:first_seen, 1:ioc_id, 2:ioc_value, 3:ioc_type, 4:threat_type, 
            # 5:fk_malware, 6:malware_alias, ..., 11:tags
            
            for row in reader:
                if len(row) < 12: continue
                
                ioc_value = row[2]
                ioc_type = row[3]
                malware_name = row[5]
                tags = row[11].lower()
                
                # We want SHA256 hashes related to Android
                if ioc_type == 'sha256_hash':
                    if 'android' in tags or 'apk' in tags or 'spyware' in tags:
                        threats.append({
                            "hash": ioc_value.lower(),
                            "name": f"{malware_name} (ThreatFox)",
                            "source": "ThreatFox"
                        })
            print(f"      ✅ Parsed {len(threats)} Android threats from ThreatFox.")
        else:
            print(f"      ❌ ThreatFox Error: {r.status_code}")
    except Exception as e:
        print(f"      ❌ Failed to fetch ThreatFox: {e}")
    return threats

def fetch_echap_data():
    print("   🔎 Fetching Echap Stalkerware Indicators...")
    threats = []
    
    # Try URLs until one works
    content = None
    for url in ECHAP_URLS:
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                content = r.text
                break
        except:
            continue
            
    if not content:
        print("      ❌ Failed to fetch Echap from all known URLs (404/Network).")
        return []

    try:
        f = io.StringIO(content)
        reader = csv.DictReader(f)
        
        for row in reader:
            if row.get('type') == 'SHA256':
                threats.append({
                    "hash": row.get('indicator', '').lower().strip(),
                    "name": row.get('app', 'Spyware.Generic'),
                    "source": "Echap"
                })
        print(f"      ✅ Parsed {len(threats)} signatures from Echap.")
    except Exception as e:
        print(f"      ❌ Error parsing Echap CSV: {e}")
        
    return threats

def fetch_malware_bazaar(api_key):
    if not api_key:
        return []

    print(f"   🔎 Querying MalwareBazaar API (Key Found)...")
    threats = []
    headers = { "API-KEY": api_key }
    
    for tag in MB_TAGS:
        try:
            data = { "query": "get_taginfo", "tag": tag, "limit": 50 }
            r = requests.post(MB_API_URL, data=data, headers=headers, timeout=10)
            if r.status_code == 200:
                json_data = r.json()
                if json_data.get("query_status") == "ok":
                    data = json_data.get("data", [])
                    for entry in data:
                        threats.append({
                            "hash": entry.get("sha256_hash"),
                            "name": entry.get("signature") or f"Malware.{tag}",
                            "source": "MalwareBazaar"
                        })
        except:
            pass
    return threats

def run_compiler():
    print("🛡️ Orion Sentinel Compiler (Robust Mode)")
    
    final_list = []
    seen_hashes = set()

    # 1. Fetch ThreatFox (New Primary Source)
    tf_threats = fetch_threatfox_data()
    for t in tf_threats:
        if t['hash'] not in seen_hashes and len(t['hash']) == 64:
            final_list.append(t)
            seen_hashes.add(t['hash'])

    # 2. Fetch Echap (Secondary Source)
    echap_threats = fetch_echap_data()
    for t in echap_threats:
        if t['hash'] not in seen_hashes and len(t['hash']) == 64:
            final_list.append(t)
            seen_hashes.add(t['hash'])

    # 3. Fetch MalwareBazaar (Optional)
    mb_key = os.environ.get("MALWARE_BAZAAR_KEY")
    api_threats = fetch_malware_bazaar(mb_key)
    for t in api_threats:
        if t['hash'] not in seen_hashes and len(t['hash']) == 64:
            final_list.append(t)
            seen_hashes.add(t['hash'])

    # 4. Manual Test Signatures
    manual_tests = [
        ("275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f", "EICAR-Test-Signature", "Manual"),
        ("5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8", "Orion-Test-Virus", "Manual")
    ]
    
    for h, n, s in manual_tests:
        if h not in seen_hashes:
            final_list.append({"hash": h, "name": n, "source": s})

    print(f"\n✅ Compiled {len(final_list)} unique signatures.")
    
    with open(OUTPUT_FILE, "w") as f:
        json.dump(final_list, f, indent=None)

if __name__ == "__main__":
    run_compiler()
