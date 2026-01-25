
import json
import requests
import csv
import io

OUTPUT_FILE = "sentinel.json"

# --- DATA SOURCES (UPDATED BY USER) ---

# 1. ThreatFox (Abuse.ch) - Recent IOCs (CSV)
# Contains recent malware hashes. We filter for Android-specific tags.
THREATFOX_CSV_URL = "https://threatfox.abuse.ch/export/csv/recent/"

# 2. Echap Stalkerware (JSON format for TinyCheck)
# This aggregates stalkerware indicators from multiple sources (including Kaspersky).
ECHAP_TINYCHECK_URL = "https://raw.githubusercontent.com/AssoEchap/stalkerware-indicators/master/generated/indicators-for-tinycheck.json"

def fetch_threatfox_data():
    print("   🔎 Fetching ThreatFox (Abuse.ch)...")
    threats = []
    try:
        r = requests.get(THREATFOX_CSV_URL, timeout=30)
        if r.status_code == 200:
            # Filter comment lines (lines starting with #)
            lines = [line for line in r.text.splitlines() if not line.startswith('#')]
            reader = csv.reader(lines)
            
            # ThreatFox CSV Column Index:
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
                
                # Filter for Android SHA256 Hashes
                if ioc_type == 'sha256_hash':
                    # We check tags OR if the malware name implies Android
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

def fetch_echap_json():
    print("   🔎 Fetching Echap Stalkerware (TinyCheck JSON)...")
    threats = []
    try:
        r = requests.get(ECHAP_TINYCHECK_URL, timeout=30)
        if r.status_code == 200:
            data = r.json()
            
            # TinyCheck format usually has an "iocs" array
            iocs = []
            if isinstance(data, dict) and "iocs" in data:
                iocs = data["iocs"]
            elif isinstance(data, list):
                iocs = data
            
            for entry in iocs:
                # Structure: { "type": "sha256", "value": "...", "comment": "..." }
                ioc_type = entry.get("type", "").lower()
                ioc_value = entry.get("value", "").lower().strip()
                comment = entry.get("comment", "Stalkerware")
                
                if ioc_type == "sha256" and len(ioc_value) == 64:
                    threats.append({
                        "hash": ioc_value,
                        "name": comment,
                        "source": "Echap"
                    })
            
            print(f"      ✅ Parsed {len(threats)} Stalkerware signatures.")
        else:
            print(f"      ❌ Echap HTTP Error: {r.status_code}")
    except Exception as e:
        print(f"      ❌ Failed to fetch Echap JSON: {e}")
    return threats

def run_compiler():
    print("🛡️ Orion Sentinel Compiler (v2.2 - User Links)")
    
    final_list = []
    seen_hashes = set()

    # 1. Fetch ThreatFox
    tf_threats = fetch_threatfox_data()
    for t in tf_threats:
        if t['hash'] not in seen_hashes and len(t['hash']) == 64:
            final_list.append(t)
            seen_hashes.add(t['hash'])

    # 2. Fetch Echap
    ec_threats = fetch_echap_json()
    for t in ec_threats:
        if t['hash'] not in seen_hashes and len(t['hash']) == 64:
            final_list.append(t)
            seen_hashes.add(t['hash'])

    # 3. Manual Test Signatures (Always included for testing)
    manual_tests = [
        ("275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f", "EICAR-Test-Signature", "Manual"),
        ("5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8", "Orion-Test-Virus", "Manual"),
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
