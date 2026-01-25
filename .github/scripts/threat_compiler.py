import requests
import json
import csv
import io
import re
import logging
import hashlib

# Configuration
OUTPUT_FILE = "sentinel.json"
TINYCHECK_URL = "https://raw.githubusercontent.com/KasperskyLab/TinyCheck/main/assets/iocs.json"
THREATFOX_CSV_URL = "https://threatfox.abuse.ch/export/csv/recent/"

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Regex for SHA256 validation
SHA256_PATTERN = re.compile(r"^[a-fA-F0-9]{64}$")

def is_valid_sha256(s):
    return bool(SHA256_PATTERN.match(s))

def fetch_tinycheck_indicators():
    """
    Fetches indicators from Kaspersky TinyCheck GitHub repository.
    Parses the JSON and extracts SHA256 hashes if available.
    """
    logging.info("Fetching Kaspersky TinyCheck indicators...")
    indicators = []
    try:
        response = requests.get(TINYCHECK_URL, timeout=15)
        response.raise_for_status()
        
        data = response.json()
        
        # TinyCheck iocs.json structure can vary, but usually contains a list of objects.
        # We will iterate through the list and extract valid SHA256 hashes.
        # Note: TinyCheck primarily focuses on domains/network IOCs, but we scan for file hashes as requested.
        
        count = 0
        if isinstance(data, list):
            for entry in data:
                # Adjust key extraction based on actual JSON structure. 
                # Common keys in IOC lists: 'ioc', 'value', 'indicator', 'type'
                ioc_value = entry.get('ioc') or entry.get('value') or entry.get('indicator')
                ioc_type = entry.get('type', '').lower()
                
                # If type is explicitly 'sha256' or looks like a hash
                if ioc_value and (ioc_type == 'sha256' or is_valid_sha256(ioc_value)):
                    indicators.append({
                        "hash": ioc_value.lower(),
                        "name": entry.get('comment') or entry.get('tag') or "Unknown Stalkerware",
                        "source": "Kaspersky TinyCheck"
                    })
                    count += 1
        
        logging.info(f" -> Found {count} hashes from TinyCheck.")
        return indicators

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logging.warning(f"TinyCheck URL not found (404). Skipping source.")
        else:
            logging.error(f"HTTP Error fetching TinyCheck: {e}")
    except Exception as e:
        logging.error(f"Error processing TinyCheck data: {e}")
    
    return []

def fetch_threatfox_indicators():
    """
    Fetches recent IOCs from ThreatFox (abuse.ch) CSV export.
    Filters for:
    - ioc_type == 'sha256_hash'
    - tags include 'android', 'apk', or 'spyware'
    """
    logging.info("Fetching ThreatFox recent indicators...")
    indicators = []
    target_tags = {'android', 'apk', 'spyware'}
    
    try:
        response = requests.get(THREATFOX_CSV_URL, timeout=30)
        response.raise_for_status()
        
        # Decode content and ignore comment lines starting with #
        content = response.content.decode('utf-8')
        lines = [line for line in content.splitlines() if not line.startswith('#')]
        
        csv_reader = csv.DictReader(lines)
        
        count = 0
        for row in csv_reader:
            # CSV Headers usually: first_seen_utc,ioc_id,ioc_value,ioc_type,threat_type,fk_malware,malware_alias,malware_printable,confidence_level,reference,tags,anonymous,reporter
            
            ioc_value = row.get('ioc_value', '').strip()
            ioc_type = row.get('ioc_type', '').strip()
            tags = row.get('tags', '').lower()
            malware_name = row.get('malware_printable', 'Unknown Malware')
            
            if ioc_type == 'sha256_hash':
                # Check if any target tag exists in the row's tags
                row_tags_set = set(t.strip() for t in tags.split(','))
                if not row_tags_set.isdisjoint(target_tags):
                    indicators.append({
                        "hash": ioc_value.lower(),
                        "name": malware_name,
                        "source": "ThreatFox"
                    })
                    count += 1
                    
        logging.info(f" -> Found {count} matching Android hashes from ThreatFox.")
        return indicators

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            logging.warning(f"ThreatFox URL not found (404). Skipping source.")
        else:
            logging.error(f"HTTP Error fetching ThreatFox: {e}")
    except Exception as e:
        logging.error(f"Error processing ThreatFox data: {e}")

    return []

def main():
    all_indicators = []
    
    # 1. Fetch Data
    all_indicators.extend(fetch_tinycheck_indicators())
    all_indicators.extend(fetch_threatfox_indicators())
    
    # 2. Add Dummy Test Hash (Required)
    test_hash_data = {
        "hash": "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
        "name": "Orion-Test-Virus",
        "source": "Manual Entry"
    }
    all_indicators.append(test_hash_data)
    
    # 3. Deduplicate
    # We use a dictionary keyed by hash to automatically handle duplicates.
    # If a hash appears twice, the last seen source/name will overwrite (or we can preserve first).
    # Here we preserve the first entry found.
    unique_db = {}
    for item in all_indicators:
        h = item['hash']
        if h not in unique_db:
            unique_db[h] = item
    
    final_list = list(unique_db.values())
    
    # 4. Write to sentinel.json
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(final_list, f, indent=4)
        logging.info(f"Successfully generated {OUTPUT_FILE} with {len(final_list)} unique entries.")
        print(f"Database generation complete. Saved to: {OUTPUT_FILE}")
        
    except IOError as e:
        logging.error(f"Failed to write output file: {e}")

if __name__ == "__main__":
    main()
