
import json
import requests
import io
import zipfile
import csv

OUTPUT_FILE = "sentinel.json"
DUMP_URL = "https://bazaar.abuse.ch/export/csv/recent/"

def fetch_and_parse():
    threats = set()
    
    print("🛡️ Orion Sentinel Compiler")
    print("   Source: MalwareBazaar Daily CSV Dump")
    
    try:
        print("⬇️ Downloading database dump...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        r = requests.get(DUMP_URL, headers=headers, stream=True, timeout=120)
        
        if r.status_code == 200:
            content_bytes = r.content
            decoded_lines = []

            # CHECK 1: Is it a ZIP file? (Magic Bytes 'PK')
            if content_bytes.startswith(b'PK'):
                print("   📦 Detected ZIP format. Extracting...")
                with zipfile.ZipFile(io.BytesIO(content_bytes)) as z:
                    filename = next((n for n in z.namelist() if n.endswith('.csv')), None)
                    if filename:
                        with z.open(filename) as f:
                            content_str = io.TextIOWrapper(f, encoding='utf-8', errors='replace').read()
                            decoded_lines = content_str.splitlines()
            
            # CHECK 2: Is it Raw Text/CSV? (Starts with # or ")
            else:
                print("   📄 Detected Raw CSV format. Parsing directly...")
                content_str = content_bytes.decode('utf-8', errors='replace')
                decoded_lines = content_str.splitlines()

            # PARSE THE LINES
            if decoded_lines:
                # Filter out comments
                rows = [line for line in decoded_lines if not line.startswith('#')]
                
                reader = csv.reader(rows)
                count = 0
                
                # CSV Structure: 
                # 0:date, 1:sha256, ... 6:file_type, ... 8:signature, 10:tags
                
                for row in reader:
                    if len(row) < 9: continue
                    
                    sha256 = row[1]
                    file_type = row[6].lower()
                    signature = row[8]
                    tags = row[10] if len(row) > 10 else ""
                    
                    # Filter for Android/APK
                    is_android = 'apk' in file_type or 'android' in tags.lower() or 'android' in signature.lower()
                    
                    if is_android and sha256 and len(sha256) == 64:
                        name = signature if signature and signature != "n/a" else "Android.Malware.Generic"
                        threats.add((sha256, name, "MalwareBazaar"))
                        count += 1
                        
                print(f"   ✅ Parsed {count} Android threats from CSV")
            else:
                print("   ⚠️ No readable content found in response.")

        else:
            print(f"   ❌ HTTP Error: {r.status_code}")
            
    except Exception as e:
        print(f"   ❌ Dump processing failed: {e}")

    # 3. Add Manual Test Signatures
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
