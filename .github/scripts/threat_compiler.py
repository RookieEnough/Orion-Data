
import json
import requests
import csv
import io

OUTPUT_FILE = "sentinel.json"

# --- DATA SOURCES ---

# 1. ThreatFox (Abuse.ch) - Recent IOCs (CSV)
THREATFOX_CSV_URL = "https://threatfox.abuse.ch/export/csv/recent/"

# 2. Echap Stalkerware (JSON format for TinyCheck)
ECHAP_TINYCHECK_URL = "https://raw.githubusercontent.com/AssoEchap/stalkerware-indicators/master/generated/indicators-for-tinycheck.json"

def fetch_threatfox_data():
    print("   🔎 Fetching ThreatFox (Abuse.ch)...")
    threats = []
    try:
        r = requests.get(THREATFOX_CSV_URL, timeout=30)
        if r.status_code == 200:
            # Filter comment lines
            lines = [line for line in r.text.splitlines() if not line.startswith('#')]
            reader = csv.reader(lines, quotechar='"', delimiter=',', skipping_skipinitialspace=True)
            
            count = 0
            for row in reader:
                # Debug print first row to check columns
                if count == 0:
                    print(f"      ℹ️ First Row Sample: {row}")
                count += 1

                if len(row) < 4: continue
                
                # Standard Layout:
                # 0: date, 1: id, 2: ioc_value, 3: ioc_type, 4: threat_type, 
                # 5: malware, 6: alias, 7: printable, ..., 11: tags
                
                # Flexible Column Finding (Basic fallback)
                ioc_value = row[2].strip().lower()
                ioc_type = row[3].strip().lower()
                
                # Grab malware name (try column 5, else 'unknown')
                malware_name = row[5].lower() if len(row) > 5 else "unknown"
                
                # Grab tags (try column 11, else empty)
                tags = row[11].lower() if len(row) > 11 else ""
                
                # Filter for SHA256 Hashes ONLY
                if ioc_type == 'sha256_hash':
                    # Heuristic Filter for Android
                    # 1. Check explicit tags
                    # 2. Check malware names known for Android
                    # 3. Check if hash looks like an APK (rare in raw csv but possible context)
                    
                    is_android = False
                    
                    android_keywords = ['android', 'apk', 'spyware', 'rat', 'banker', 'sms', 'stealer']
                    if any(k in tags for k in android_keywords):
                        is_android = True
                    elif any(k in malware_name for k in ['hydra', 'cerberus', 'joker', 'alien', 'ermac', 'flu', 'teabot', 'anubis', 'hiddad']):
                        is_android = True
                    
                    if is_android:
                        threats.append({
                            "hash": ioc_value,
                            "name": f"{malware_name} (ThreatFox)",
                            "source": "ThreatFox"
                        })
                        
            print(f"      ✅ Parsed {len(threats)} Android threats from {count} total rows.")
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
            try:
                data = r.json()
            except:
                print("      ❌ Failed to parse Echap JSON text.")
                return []

            # Structure detection
            iocs = []
            if isinstance(data, list):
                print("      ℹ️ Detected List structure")
                iocs = data
            elif isinstance(data, dict):
                print(f"      ℹ️ Detected Dict structure. Keys: {list(data.keys())}")
                if "iocs" in data:
                    iocs = data["iocs"]
                elif "indicators" in data:
                    iocs = data["indicators"]
                else:
                    # Try to find any list in values
                    for k, v in data.items():
                        if isinstance(v, list):
                            iocs.extend(v)

            for entry in iocs:
                # Structure: { "type": "sha256", "value": "...", "comment": "..." }
                # Be defensive with keys
                if not isinstance(entry, dict): continue
                
                ioc_type = entry.get("type", "").lower()
                ioc_value = entry.get("value", "").lower().strip()
                # Some formats use 'indicator' instead of 'value'
                if not ioc_value:
                    ioc_value = entry.get("indicator", "").lower().strip()

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
    print("🛡️ Orion Sentinel Compiler (v2.3 - Robust Parsing)")
    
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

    # 3. Manual Test Signatures (Always included)
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
    
    # Save to file
    with open(OUTPUT_FILE, "w") as f:
        json.dump(final_list, f, indent=None)

if __name__ == "__main__":
    run_compiler()
