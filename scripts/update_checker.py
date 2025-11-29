#!/usr/bin/env python3
from scraper import GetModsApkScraper
from utils import load_config
import os

def check_updates():
    scraper = GetModsApkScraper()
    config = load_config()
    updates_available = False
    
    for apk in config['tracked_apks']:
        print(f"Checking {apk['name']}...")
        current_version = scraper.get_current_version(apk['base_url'])
        
        if current_version and current_version != apk['current_version']:
            print(f"UPDATE AVAILABLE: {apk['name']} {apk['current_version']} -> {current_version}")
            updates_available = True
        else:
            print(f"No update for {apk['name']}")
    
    # Set output for GitHub Actions
    if updates_available:
        print("::set-output name=updates_available::true")
    else:
        print("::set-output name=updates_available::false")
    
    return updates_available

if __name__ == "__main__":
    check_updates()
