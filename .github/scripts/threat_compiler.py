
import json
import requests
import io
import zipfile
import csv

OUTPUT_FILE = "sentinel.json"
# Use the Daily CSV Dump (Stable, No API Key needed)
DUMP_URL = "https://bazaar.abuse.ch/export/csv/recent/"

def fetch_and_parse():
    threats = set()
    
    print("🛡️ Orion Sentinel Compiler")
    print("   Source: MalwareBazaar Daily CSV Dump")
    
    try:
        print("⬇️ Downloading database dump...")
        r = requests.get(DUMP_URL, stream=True, timeout=60)
        
        if r.status_code == 200:
            print("   📦 Extracting and parsing...")
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                # The zip contains one file usually named 'recent.csv'
                filename = z.namelist()[0]
                with z.open(filename) as f:
                    # Decode bytes to string
                    content = io.TextIOWrapper(f, encoding='utf-8', errors='replace')
                    
                    # Iterate manually to skip comment lines starting with #
                    rows = []
                    for line in content:
                        if not line.startswith('#'):
                            rows.append(line)
                    
                    reader = csv.reader(rows)
                    count = 0
                    
                    # CSV Structure: 
                    # 0:date, 1:sha256, 2:md5, 3:sha1, 4:reporter, 5:filename, 6:file_type, 7:mime, 8:signature, ...
                    
                    for row in reader:
                        if len(row) < 9: continue
                        
                        sha256 = row[1]
                        file_type = row[6].lower()
                        signature = row[8]
                        tags = row[10] if len(row) > 10 else ""
                        
                        # Filter for Android/APK
                        is_android = 'apk' in file_type or 'android' in tags.lower() or 'android' in signature.lower()
                        
                        if is_android and sha256 and len(sha256) == 64:
                            # Use signature if available, else generic name
                            name = signature if signature and signature != "n/a" else "Android.Malware.Generic"
                            threats.add((sha256, name, "MalwareBazaar"))
                            count += 1
                            
                    print(f"   ✅ Parsed {count} Android threats from CSV")
        else:
            print(f"   ❌ HTTP Error: {r.status_code}")
            
    except Exception as e:
        print(f"   ❌ Dump processing failed: {e}")

    # 3. Add Manual Test Signatures (Safe for testing)
    threats.add(("275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f", "EICAR-Test-Signature", "Manual"))
    threats.add(("5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8", "Orion-Test-Virus", "Manual"))

    # Compile List
    final_list = []
    for h, name, src in threats:
        final_list.append({
            "hash": h,
            "name": name,
            "source": src
        })
    
    print(f"✅ Compiled {len(final_list)} total signatures.")
    
    with open(OUTPUT_FILE, "w") as f:
        json.dump(final_list, f)

if __name__ == "__main__":
    fetch_and_parse()
