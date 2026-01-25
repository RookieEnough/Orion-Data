#!/usr/bin/env python3
"""
Threat Intelligence Database Generator for Android Antivirus App
Generates sentinel.json with indicators from Kaspersky TinyCheck and ThreatFox
"""

import requests
import json
import csv
from io import StringIO
import hashlib
from typing import List, Dict, Set
from datetime import datetime
import logging
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('threat_intel.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Configuration
CONFIG = {
    "kaspersky_url": "https://raw.githubusercontent.com/KasperskyLab/TinyCheck/master/backend/app/utils/ioc.json",
    "threatfox_url": "https://threatfox.abuse.ch/export/csv/recent/",
    "output_file": "sentinel.json",
    "user_agent": "AndroidThreatIntel/1.0",
    "timeout": 30,
    "sources": {
        "KASPERSKY": "Kaspersky TinyCheck",
        "THREATFOX": "ThreatFox (abuse.ch)",
        "TEST": "Internal Test"
    }
}

class ThreatIntelCollector:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': CONFIG['user_agent']})
        self.collected_indicators: Dict[str, Dict] = {}
        
    def validate_sha256(self, hash_str: str) -> bool:
        """Validate if string is a valid SHA256 hash."""
        if not isinstance(hash_str, str):
            return False
        hash_str = hash_str.strip().lower()
        if len(hash_str) != 64:
            return False
        try:
            # Check if all characters are valid hex
            int(hash_str, 16)
            return True
        except ValueError:
            return False
    
    def fetch_kaspersky_indicators(self) -> List[Dict]:
        """Fetch and parse Kaspersky TinyCheck indicators."""
        indicators = []
        try:
            logger.info(f"Fetching Kaspersky indicators from {CONFIG['kaspersky_url']}")
            response = self.session.get(
                CONFIG['kaspersky_url'], 
                timeout=CONFIG['timeout']
            )
            
            if response.status_code == 404:
                logger.warning("Kaspersky TinyCheck URL returned 404 - data may have moved")
                return indicators
                
            response.raise_for_status()
            
            data = response.json()
            
            # Process indicators based on TinyCheck format
            for item in data:
                # Handle different possible structures
                hash_value = None
                name = "Unknown"
                
                if isinstance(item, dict):
                    # Try different possible field names
                    hash_value = item.get('hash') or item.get('sha256') or item.get('md5')
                    name = item.get('name', 'Unknown')
                elif isinstance(item, str):
                    # Might be just a hash string
                    hash_value = item
                    name = "Malware"
                
                if hash_value and self.validate_sha256(hash_value):
                    indicators.append({
                        'hash': hash_value.lower(),
                        'name': str(name),
                        'source': CONFIG['sources']['KASPERSKY']
                    })
                    
            logger.info(f"Found {len(indicators)} valid SHA256 hashes from Kaspersky")
            return indicators
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch Kaspersky data: {e}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Kaspersky JSON: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error processing Kaspersky data: {e}")
            return []
    
    def fetch_threatfox_indicators(self) -> List[Dict]:
        """Fetch and parse ThreatFox CSV, filtering for Android/SHA256 indicators."""
        indicators = []
        try:
            logger.info(f"Fetching ThreatFox indicators from {CONFIG['threatfox_url']}")
            response = self.session.get(
                CONFIG['threatfox_url'], 
                timeout=CONFIG['timeout']
            )
            
            if response.status_code == 404:
                logger.warning("ThreatFox URL returned 404 - data may have moved")
                return indicators
                
            response.raise_for_status()
            
            # Parse CSV data
            csv_data = StringIO(response.text)
            
            # Find header line (starts with '#')
            lines = response.text.split('\n')
            header_line = None
            for i, line in enumerate(lines):
                if line.startswith('# first_seen_utc'):
                    header_line = i
                    break
            
            if header_line is None:
                logger.error("Could not find CSV header in ThreatFox data")
                return indicators
            
            # Read CSV from header line
            csv_data = StringIO('\n'.join(lines[header_line:]))
            csv_reader = csv.DictReader(csv_data)
            
            # Required fields
            required_fields = ['ioc_type', 'ioc_value', 'malware_printable', 'tags']
            
            for row in csv_reader:
                # Check if all required fields exist
                if not all(field in row for field in required_fields):
                    continue
                
                # Filter: SHA256 hash and Android/APK/Spyware tags
                ioc_type = row['ioc_type'].strip()
                ioc_value = row['ioc_value'].strip()
                tags = row['tags'].lower()
                
                if ioc_type != 'sha256_hash':
                    continue
                
                # Check for Android-related tags
                target_tags = {'android', 'apk', 'spyware'}
                if not any(tag in tags for tag in target_tags):
                    continue
                
                # Validate SHA256
                if not self.validate_sha256(ioc_value):
                    continue
                
                indicators.append({
                    'hash': ioc_value.lower(),
                    'name': row['malware_printable'].strip() or 'Unknown Threat',
                    'source': CONFIG['sources']['THREATFOX']
                })
            
            logger.info(f"Found {len(indicators)} valid SHA256 hashes from ThreatFox")
            return indicators
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch ThreatFox data: {e}")
            return []
        except csv.Error as e:
            logger.error(f"Failed to parse ThreatFox CSV: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error processing ThreatFox data: {e}")
            return []
    
    def add_test_indicator(self):
        """Add the dummy test hash."""
        test_hash = "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8"
        
        # Verify it's a valid SHA256 (hash of 'password')
        if hashlib.sha256(b'password').hexdigest() == test_hash:
            self.collected_indicators[test_hash] = {
                'hash': test_hash,
                'name': 'Orion-Test-Virus',
                'source': CONFIG['sources']['TEST']
            }
            logger.info(f"Added test indicator: {test_hash}")
        else:
            logger.error("Test hash validation failed!")
    
    def deduplicate_indicators(self, indicators: List[Dict]) -> None:
        """Deduplicate indicators by hash."""
        for indicator in indicators:
            hash_val = indicator['hash']
            if hash_val not in self.collected_indicators:
                self.collected_indicators[hash_val] = indicator
            else:
                # If duplicate, keep the one with more specific name
                existing = self.collected_indicators[hash_val]
                if existing['name'] == 'Unknown' and indicator['name'] != 'Unknown':
                    self.collected_indicators[hash_val] = indicator
    
    def generate_output(self) -> List[Dict]:
        """Generate final sorted output list."""
        output = list(self.collected_indicators.values())
        # Sort by hash for consistent output
        output.sort(key=lambda x: x['hash'])
        return output
    
    def save_to_file(self, data: List[Dict]) -> bool:
        """Save indicators to JSON file."""
        try:
            output = {
                'metadata': {
                    'generated_at': datetime.utcnow().isoformat() + 'Z',
                    'total_indicators': len(data),
                    'sources': list(CONFIG['sources'].values())
                },
                'indicators': data
            }
            
            with open(CONFIG['output_file'], 'w') as f:
                json.dump(output['indicators'], f, indent=2)
            
            logger.info(f"Successfully saved {len(data)} indicators to {CONFIG['output_file']}")
            logger.info(f"Metadata: {output['metadata']}")
            return True
            
        except IOError as e:
            logger.error(f"Failed to write to {CONFIG['output_file']}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error saving file: {e}")
            return False
    
    def run(self):
        """Main execution method."""
        logger.info("=" * 60)
        logger.info("Starting threat intelligence collection")
        logger.info("=" * 60)
        
        # Fetch from both sources
        kaspersky_indicators = self.fetch_kaspersky_indicators()
        threatfox_indicators = self.fetch_threatfox_indicators()
        
        # Deduplicate and combine
        self.deduplicate_indicators(kaspersky_indicators)
        self.deduplicate_indicators(threatfox_indicators)
        
        # Add test indicator
        self.add_test_indicator()
        
        # Generate output
        final_data = self.generate_output()
        
        # Save to file
        if self.save_to_file(final_data):
            logger.info("Threat intelligence database generation completed successfully")
            return True
        else:
            logger.error("Failed to save threat intelligence database")
            return False

def main():
    """Main entry point."""
    collector = ThreatIntelCollector()
    
    try:
        success = collector.run()
        return 0 if success else 1
    except KeyboardInterrupt:
        logger.info("Process interrupted by user")
        return 130
    except Exception as e:
        logger.error(f"Unexpected error in main execution: {e}")
        return 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
