
import json
import requests
import csv
import io

OUTPUT_FILE = "sentinel.json"

# --- DATA SOURCES ---

# 1. ThreatFox (Abuse.ch) - Free, Daily Malware List
# We will download the recent CSV and filter for Android threats.
THREATFOX_CSV_URL = "https://threatfox.abuse.ch/export/csv/recent/"

# 2. Kaspersky TinyCheck (GitHub) - Stable Stalkerware List
# Maintained by Kaspersky Lab, specifically for stalkerware/spyware.
TINYCHECK_URL = "https://raw.githubusercontent.com/KasperskyLab/tinycheck/main/assets/iocs.json"

def fetch_threatfox_data():
    print("   🔎 Fetching ThreatFox (Abuse.ch)...")
    threats = []
    try:
        r = requests.get(THREATFOX_CSV_URL, timeout=30)
        if r.status_code == 200:
            # Filter comment lines
            lines = [line for line in r.text.splitlines() if not line.startswith('#')]
            reader = csv.reader(lines)
            
            # ThreatFox CSV Column Index (Standard):
            # 2: ioc_value (The Hash/URL)
            # 3: ioc_type (sha256_hash, ip:port, etc)
            # 5: malware (Name)
            # 11: tags (android, rat, etc)
            
            for row in reader:
                if len(row) < 12: continue
                
                ioc_value = row[2].strip().lower()
                ioc_type = row[3].strip()
                malware_name = row[5]
                tags = row[11].lower()
                
                # Filter for SHA256 Hashes ONLY
                if ioc_type == 'sha256_hash':
                    # We check tags OR if the malware name implies Android
                    # Common Android malware families: hydra, cerberus, alien, joker, hiddad
                    if 'android' in tags or 'apk' in tags or 'spyware' in tags or \
                       'hydra' in malware_name.lower() or 'cerberus' in malware_name.lower() or \
                       'joker' in malware_name.lower():
                        threats.append({
                            "hash": ioc_value,
                            "name": f"{malware_name} (ThreatFox)",
                            "source": "ThreatFox"
                        })
            print(f"      ✅ Parsed {len(threats)} Android threats from ThreatFox.")
        else:
            print(f"      ❌ ThreatFox HTTP Error: {r.status_code}")
    except Exception as e:
        print(f"      ❌ Failed to fetch ThreatFox: {e}")
    return threats

def fetch_tinycheck_data():
    print("   🔎 Fetching Kaspersky TinyCheck (GitHub)...")
    threats = []
    try:
        r = requests.get(TINYCHECK_URL, timeout=30)
        if r.status_code == 200:
            data = r.json()
            # Structure: "iocs": [ { "type": "sha256", "value": "...", "comment": "..." } ]
            if "iocs" in data:
                for entry in data["iocs"]:
                    if entry.get("type") == "sha256":
                        threats.append({
                            "hash": entry.get("value", "").lower().strip(),
                            "name": entry.get("comment", "Stalkerware.Generic"),
                            "source": "Kaspersky/TinyCheck"
                        })
            print(f"      ✅ Parsed {len(threats)} Stalkerware signatures.")
        else:
            print(f"      ❌ TinyCheck HTTP Error: {r.status_code}")
    except Exception as e:
        print(f"      ❌ Failed to fetch TinyCheck: {e}")
    return threats

def run_compiler():
    print("🛡️ Orion Sentinel Compiler (v2.1)")
    
    final_list = []
    seen_hashes = set()

    # 1. Fetch ThreatFox
    tf_threats = fetch_threatfox_data()
    for t in tf_threats:
        if t['hash'] not in seen_hashes and len(t['hash']) == 64:
            final_list.append(t)
            seen_hashes.add(t['hash'])

    # 2. Fetch TinyCheck (Kaspersky)
    tc_threats = fetch_tinycheck_data()
    for t in tc_threats:
        if t['hash'] not in seen_hashes and len(t['hash']) == 64:
            final_list.append(t)
            seen_hashes.add(t['hash'])

    # 3. Always add Manual Test Signatures (So app works even if network fails)
    manual_tests = [
        # EICAR Test File (Standard Anti-Virus Test String)
        ("275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f", "EICAR-Test-Signature", "Manual"),
        # Orion Test Hash (For debugging)
        ("5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8", "Orion-Test-Virus", "Manual"),
        # A common generic malware hash for testing UI
        ("8a39875e63821733393933393339333933393339333933393339333933393339", "Generic.Trojan.Dropper", "Manual")
    ]
    
    for h, n, s in manual_tests:
        if h not in seen_hashes:
            final_list.append({"hash": h, "name": n, "source": s})
            seen_hashes.add(h)

    print(f"\n✅ Compiled {len(final_list)} unique signatures.")
    
    with open(OUTPUT_FILE, "w") as f:
        json.dump(final_list, f, indent=None)

if __name__ == "__main__":
    run_compiler()
