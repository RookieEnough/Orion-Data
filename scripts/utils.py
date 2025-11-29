import requests
from bs4 import BeautifulSoup
import re
import json
import os

def setup_session():
    """Setup requests session with headers"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    })
    return session

def extract_version_info(text):
    """Extract version from text"""
    version_pattern = r'v?(\d+\.\d+\.\d+)'
    match = re.search(version_pattern, text)
    return match.group(0) if match else None

def load_config():
    """Load APK configuration"""
    with open('config/apk-list.json', 'r') as f:
        return json.load(f)

def save_config(config):
    """Save APK configuration"""
    with open('config/apk-list.json', 'w') as f:
        json.dump(config, f, indent=2)
