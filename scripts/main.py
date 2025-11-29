#!/usr/bin/env python3
import argparse
from scraper import GetModsApkScraper
from downloader import APKDownloader
from utils import load_config, save_config
import os
import re

def normalize_version(version):
    """Normalize version string for comparison"""
    if not version:
        return ""
    # Remove 'v' prefix and any non-version characters
    version = re.sub(r'^v', '', str(version).strip())
    # Keep only version numbers and dots
    version = re.sub(r'[^\d.]', '', version)
    return version

def main():
    parser = argparse.ArgumentParser(description='APK Scraper for GetModsApk')
    parser.add_argument('--auto', action='store_true', help='Auto process all APKs')
    parser.add_argument('--manual', action='store_true', help='Manual download')
    parser.add_argument('--url', help='APK URL for manual download')
    parser.add_argument('--tag', help='Release tag for manual download')
    parser.add_argument('--name', help='APK name for manual download')
    parser.add_argument('--force', action='store_true', help='Force download even if version matches')
    
    args = parser.parse_args()
    
    github_token = os.getenv('GITHUB_TOKEN')
    repo_name = os.getenv('GITHUB_REPOSITORY')
    
    print(f"🔑 GitHub Token: {'Provided' if github_token else 'Not provided'}")
    print(f"🏠 Repository: {repo_name}")
    
    scraper = GetModsApkScraper()
    downloader = APKDownloader(github_token)
    
    if args.auto:
        print("🚀 Running auto scraper...")
        config = load_config()
        downloaded_count = 0
        
        for apk in config['tracked_apks']:
            print(f"\n" + "="*50)
            print(f"🔍 Processing {apk['name']}...")
            print(f"🌐 URL: {apk['base_url']}")
            
            # Check current version
            current_version = scraper.get_current_version(apk['base_url'])
            if not current_version:
                print(f"❌ Could not determine current version for {apk['name']}")
                continue
                
            normalized_current = normalize_version(current_version)
            normalized_config = normalize_version(apk['current_version'])
            
            print(f"📋 Website version: {current_version}")
            print(f"📋 Config version: {apk['current_version']}")
            print(f"📊 Normalized comparison: {normalized_current} vs {normalized_config}")
            
            should_download = args.force or (normalized_current != normalized_config)
            
            if should_download:
                if args.force:
                    print(f"🔄 Force downloading: {current_version}")
                else:
                    print(f"🆕 New version found: {current_version} (was {apk['current_version']})")
                
                # Get download link
                download_url = scraper.get_download_links(apk['base_url'])
                if download_url:
                    print(f"🔗 Download URL obtained: {download_url}")
                    filename = f"{apk['name'].replace(' ', '-').lower()}-{current_version}.apk"
                    filepath = downloader.download_apk(download_url, filename)
                    
                    if filepath and os.path.exists(filepath):
                        file_size = os.path.getsize(filepath) / (1024 * 1024)  # MB
                        print(f"✅ Downloaded: {filepath} ({file_size:.2f} MB)")
                        downloaded_count += 1
                        
                        if github_token:
                            print(f"📤 Attempting to upload to GitHub releases...")
                            success = downloader.upload_to_release(
                                repo_name, 
                                filepath, 
                                apk['release_tag'], 
                                current_version
                            )
                            if success:
                                downloader.update_apk_list(apk['name'], current_version)
                                print(f"🎉 Successfully completed for {apk['name']}")
                            else:
                                print(f"❌ Failed to upload to release for {apk['name']}")
                        else:
                            print(f"⚠️  No GitHub token - skipping release upload")
                    else:
                        print(f"❌ Failed to download APK or file doesn't exist")
                else:
                    print(f"❌ Could not find download link for {apk['name']}")
            else:
                print(f"✅ No update available for {apk['name']}")
        
        print(f"\n" + "="*50)
        print(f"📊 Summary: Downloaded {downloaded_count} new APK(s)")
        
    elif args.manual and args.url and args.tag and args.name:
        print("🛠️ Running manual download...")
        download_url = scraper.get_download_links(args.url)
        
        if download_url:
            current_version = scraper.get_current_version(args.url) or "unknown"
            filename = f"{args.name.replace(' ', '-').lower()}-{current_version}.apk"
            filepath = downloader.download_apk(download_url, filename)
            
            if filepath and github_token:
                downloader.upload_to_release(
                    repo_name,
                    filepath,
                    args.tag,
                    current_version
                )
        else:
            print("❌ Could not find download link")
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
