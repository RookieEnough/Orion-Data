
import json
import requests
import time
import os
import csv
import io

OUTPUT_FILE = "sentinel.json"

# --- CONFIGURATION ---
# 1. MalwareBazaar API (Requires Key for reliable access now)
MB_API_URL = "https://mb-api.abuse.ch/api/v1/"
MB_TAGS = ["android", "apk", "bankbot", "hiddad", "joker"]

# 2. Public GitHub Feeds (No Key Required - Reliable Fallback)
# Echap Stalkerware Indicators: Excellent source for Android spyware
ECHAP_CSV_URL = "https://raw.githubusercontent.com/AssoEchap/stalkerware-indicators/master/generated/stalkerware.csv"

def fetch_malware_bazaar(api_key):
    """
    Queries MalwareBazaar. Requires API Key to avoid 401.
    """
    if not api_key:
        print("   ⚠️ No MalwareBazaar API Key found. Skipping API to avoid 401.")
        return []

    print(f"   🔎 Querying MalwareBazaar API...")
    results = []
    headers = { "API-KEY": api_key }
    
    for tag in MB_TAGS:
        try:
            data = { "query": "get_taginfo", "tag": tag, "limit": 100 }
            r = requests.post(MB_API_URL, data=data, headers=headers, timeout=15)
            
            if r.status_code == 200:
                json_data = r.json()
                if json_data.get("query_status") == "ok":
                    batch = json_data.get("data", [])
                    print(f"      ↳ Tag '{tag}': Found {len(batch)} entries.")
                    for entry in batch:
                        results.append({
                            "hash": entry.get("sha256_hash"),
                            "name": entry.get("signature") or f"MalwareBazaar.{tag}",
                            "source": "MalwareBazaar"
                        })
                else:
                    print(f"      ⚠️ Tag '{tag}': {json_data.get('query_status')}")
            elif r.status_code == 401:
                print(f"      ❌ API Error 401: Unauthorized. Check your API Key.")
                break # Stop trying tags if key is invalid
            else:
                print(f"      ❌ API Error: {r.status_code}")
                
        except Exception as e:
            print(f"      ❌ Connection failed: {e}")
        
        time.sleep(1) # Rate limit politeness
        
    return results

def fetch_echap_indicators():
    """
    Fetches public Stalkerware indicators from GitHub.
    Reliable source for Android threats.
    """
    print(f"   🔎 Downloading Echap Stalkerware Indicators (GitHub)...")
    results = []
    try:
        r = requests.get(ECHAP_CSV_URL, timeout=30)
        if r.status_code == 200:
            # Parse CSV
            # Header usually: app, type, indicator, ...
            f = io.StringIO(r.text)
            reader = csv.DictReader(f)
            
            for row in reader:
                # We specifically look for SHA256 hashes
                indicator = row.get("indicator", "").strip()
                ind_type = row.get("type", "").lower()
                
                # Check if it looks like a SHA256 hash (64 hex chars)
                if ind_type == "sha256" and len(indicator) == 64:
                    results.append({
                        "hash": indicator,
                        "name": f"Spyware.{row.get('app', 'Generic')}",
                        "source": "Echap"
                    })
            print(f"      ✅ Parsed {len(results)} signatures from CSV.")
        else:
            print(f"      ❌ HTTP Error: {r.status_code}")
    except Exception as e:
        print(f"      ❌ Failed to fetch Echap list: {e}")
        
    return results

def run_compiler():
    print("🛡️ Orion Sentinel Compiler (Multi-Source)")
    
    unique_threats = {} # Map hash -> data to prevent duplicates
    
    # 1. Fetch from Public GitHub Feed (Echap)
    echap_data = fetch_echap_indicators()
    for item in echap_data:
        unique_threats[item['hash']] = item

    # 2. Fetch from MalwareBazaar (Optional)
    # Check environment variable for key
    mb_key = os.environ.get("MALWARE_BAZAAR_KEY")
    mb_data = fetch_malware_bazaar(mb_key)
    for item in mb_data:
        unique_threats[item['hash']] = item

    # 3. Add Manual Test Signatures
    manual_threats = [
        ("275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f", "EICAR-Test-Signature"),
        ("5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8", "Orion-Test-Virus")
    ]
    
    for h, n in manual_threats:
        unique_threats[h] = {
            "hash": h,
            "name": n,
            "source": "Manual"
        }

    # 4. Export
    final_list = list(unique_threats.values())
    print(f"\n✅ Compiled {len(final_list)} unique signatures.")
    
    if len(final_list) < 5:
        print("   ⚠️ Warning: Low signature count. Ensure internet access is enabled.")

    with open(OUTPUT_FILE, "w") as f:
        json.dump(final_list, f, indent=None)

if __name__ == "__main__":
    run_compiler()
