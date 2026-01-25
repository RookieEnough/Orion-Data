import requests
import json
import logging
import re

# --- Configuration ---
OUTPUT_FILE = "sentinel.json"

# Source 1: AssoEchap Stalkerware (The active repo, replaces Te-k)
STALKERWARE_URL = "https://raw.githubusercontent.com/AssoEchap/stalkerware-indicators/master/generated/indicators-for-tinycheck.json"

# Source 2: ThreatFox Public Export (Bypasses API Key / 401 errors)
THREATFOX_EXPORT_URL = "https://threatfox.abuse.ch/export/json/recent/"

# Source 3: MalwareBazaar Recent (Supplementary source for APKs)
MALWAREBAZAAR_URL = "https://mb-api.abuse.ch/api/v1/"

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)

def fetch_stalkerware_indicators():
    """
    Fetches indicators from AssoEchap (Community Stalkerware).
    """
    logging.info("Fetching Stalkerware indicators (AssoEchap)...")
    indicators = []
    try:
        response = requests.get(STALKERWARE_URL, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # This JSON is a list of objects.
        count = 0
        for entry in data:
            # We strictly want file hashes, not domains/IPs
            if entry.get('type') == 'sha256':
                indicators.append({
                    "hash": entry.get('value', '').lower(),
                    "name": entry.get('comment') or "Stalkerware (Community)",
                    "source": "AssoEchap Stalkerware"
                })
                count += 1
        
        logging.info(f" -> Found {count} hashes from Stalkerware source.")
        return indicators

    except Exception as e:
        logging.error(f"Failed to fetch Stalkerware data: {e}")
        return []

def fetch_threatfox_export():
    """
    Fetches the Public JSON Export from ThreatFox.
    Client-side filters for 'android' tags to avoid API auth issues.
    """
    logging.info("Fetching ThreatFox Public JSON Export...")
    indicators = []
    target_tags = {'android', 'apk', 'spyware', 'rat', 'bankbot'}
    
    try:
        response = requests.get(THREATFOX_EXPORT_URL, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        # The export is a dict { "query_status": "ok", "data": [...] }
        entries = data.get('data', [])
        count = 0
        
        for entry in entries:
            # Filter 1: Must be SHA256
            if entry.get('ioc_type') != 'sha256_hash':
                continue
            
            # Filter 2: Must match Android tags
            tags = entry.get('tags')
            if tags:
                # Convert list of tags to lowercase set
                entry_tags = set(t.lower() for t in tags)
                if not entry_tags.isdisjoint(target_tags):
                    indicators.append({
                        "hash": entry.get('ioc', '').lower(),
                        "name": entry.get('malware_printable') or "Android Malware",
                        "source": "ThreatFox Export"
                    })
                    count += 1
                    
        logging.info(f" -> Found {count} Android hashes in ThreatFox Export.")
        return indicators

    except Exception as e:
        logging.error(f"Failed to fetch ThreatFox Export: {e}")
        return []

def fetch_malwarebazaar_recent():
    """
    Fetches recent Android samples from MalwareBazaar.
    Useful fallback if ThreatFox is empty.
    """
    logging.info("Querying MalwareBazaar for recent Android APKs...")
    indicators = []
    
    # MalwareBazaar 'query' API is public for recent additions
    payload = {"query": "get_recent", "selector": "time"}
    
    try:
        response = requests.post(MALWAREBAZAAR_URL, data=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        if data.get('query_status') != 'ok':
            return []

        entries = data.get('data', [])
        count = 0
        
        for entry in entries:
            # Filter for APK file type or Android tags
            file_type = entry.get('file_type', '').lower()
            tags = entry.get('tags') or []
            is_android = False
            
            if file_type == 'apk':
                is_android = True
            elif tags:
                tag_set = set(t.lower() for t in tags)
                if 'android' in tag_set or 'apk' in tag_set:
                    is_android = True
            
            if is_android and entry.get('sha256_hash'):
                indicators.append({
                    "hash": entry.get('sha256_hash', '').lower(),
                    "name": entry.get('signature') or "Unknown Android Sample",
                    "source": "MalwareBazaar"
                })
                count += 1
                
        logging.info(f" -> Found {count} hashes from MalwareBazaar.")
        return indicators

    except Exception as e:
        logging.error(f"Failed to fetch MalwareBazaar: {e}")
        return []

def main():
    all_indicators = []

    # 1. Fetch from robust sources
    all_indicators.extend(fetch_stalkerware_indicators())
    all_indicators.extend(fetch_threatfox_export())
    all_indicators.extend(fetch_malwarebazaar_recent())

    # 2. Add Dummy Test Hash (Strict Requirement)
    test_hash = {
        "hash": "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
        "name": "Orion-Test-Virus",
        "source": "Manual Entry"
    }
    all_indicators.append(test_hash)

    # 3. Deduplicate
    unique_db = {}
    for item in all_indicators:
        h = item['hash']
        # Strict Hex Validation (64 chars)
        if h and len(h) == 64 and re.match(r'^[a-fA-F0-9]{64}$', h):
            if h not in unique_db:
                unique_db[h] = item

    final_list = list(unique_db.values())

    # 4. Save
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(final_list, f, indent=4)
        
        logging.info("--- Summary ---")
        logging.info(f"Total Unique Hashes: {len(final_list)}")
        logging.info(f"Database saved to: {OUTPUT_FILE}")
        
    except IOError as e:
        logging.error(f"Error writing to file: {e}")

if __name__ == "__main__":
    main()
