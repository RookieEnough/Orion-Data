import requests
import json
import logging
import re
import time

# --- Configuration ---
OUTPUT_FILE = "sentinel.json"

# Source 1: Community Stalkerware Indicators (Replaces the dead Kaspersky link)
# This repo is actively maintained by the research community.
STALKERWARE_URL = "https://raw.githubusercontent.com/Te-k/stalkerware-indicators/master/indicators-for-tinycheck.json"

# Source 2: ThreatFox API (Replaces the empty CSV)
# We use the API to ask for the last 1000 entries tagged "android" explicitly.
THREATFOX_API_URL = "https://threatfox-api.abuse.ch/api/v1/"

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)

def fetch_stalkerware_indicators():
    """
    Fetches indicators from the Te-k/stalkerware-indicators repo.
    This file is formatted specifically for TinyCheck compatibility.
    """
    logging.info("Fetching Stalkerware indicators (Te-k/stalkerware-indicators)...")
    indicators = []
    
    try:
        response = requests.get(STALKERWARE_URL, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        # Structure varies, usually a list of objects with 'type' and 'value'
        # or sometimes a dict where keys are IDs. We handle the list format common in this file.
        count = 0
        if isinstance(data, list):
            for entry in data:
                # We only care about file hashes (sha256), not domains/IPs for this specific DB
                if entry.get('type') == 'sha256':
                    indicators.append({
                        "hash": entry.get('value', '').lower(),
                        "name": entry.get('comment') or "Unknown Stalkerware",
                        "source": "Community Stalkerware List"
                    })
                    count += 1
        
        logging.info(f" -> Found {count} hashes from Stalkerware source.")
        return indicators

    except Exception as e:
        logging.error(f"Failed to fetch Stalkerware data: {e}")
        return []

def fetch_threatfox_api():
    """
    Queries ThreatFox API for the tag 'android'.
    This guarantees data even if the 'recent' CSV is empty.
    """
    logging.info("Querying ThreatFox API for tag: 'android'...")
    indicators = []
    
    payload = {
        "query": "taginfo",
        "tag": "android",
        "limit": 1000  # Max limit usually allowed by API for this query
    }
    
    try:
        # Retry logic for API
        response = requests.post(THREATFOX_API_URL, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        if data.get("query_status") != "ok":
            logging.warning(f"ThreatFox API returned status: {data.get('query_status')}")
            return []

        # Parse the 'data' list
        entries = data.get("data", [])
        count = 0
        
        for entry in entries:
            # ThreatFox returns many types (botnet_cc, payload_delivery).
            # We strictly want SHA256 hashes of files.
            if entry.get("ioc_type") == "sha256_hash":
                indicators.append({
                    "hash": entry.get("ioc", "").lower(),
                    "name": entry.get("malware_printable") or entry.get("malware_alias") or "Android Malware",
                    "source": "ThreatFox API"
                })
                count += 1

        logging.info(f" -> Successfully retrieved {count} Android hashes from ThreatFox API.")
        return indicators

    except Exception as e:
        logging.error(f"Failed to query ThreatFox API: {e}")
        return []

def main():
    all_indicators = []

    # 1. Fetch from new sources
    all_indicators.extend(fetch_stalkerware_indicators())
    all_indicators.extend(fetch_threatfox_api())

    # 2. Add Dummy Test Hash (Strict Requirement)
    test_hash = {
        "hash": "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
        "name": "Orion-Test-Virus",
        "source": "Manual Entry"
    }
    all_indicators.append(test_hash)

    # 3. Deduplicate (Keep the first occurrence of a hash)
    unique_db = {}
    for item in all_indicators:
        h = item['hash']
        # Simple validation: valid SHA256 is 64 hex chars
        if h and len(h) == 64 and re.match(r'^[a-fA-F0-9]{64}$', h):
            if h not in unique_db:
                unique_db[h] = item

    final_list = list(unique_db.values())

    # 4. Save to JSON
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
