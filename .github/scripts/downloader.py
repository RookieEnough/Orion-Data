from utils import setup_session, load_config, save_config
import requests
import os
from github import Github
import re

class APKDownloader:
    def __init__(self, github_token=None):
        self.session = setup_session()
        self.gh = Github(github_token) if github_token else None
    
    def download_apk(self, url, filename):
        """Download APK file with proper handling"""
        try:
            print(f"📥 Downloading APK from {url}")
            
            # Ensure filename ends with .apk
            if not filename.lower().endswith('.apk'):
                filename += '.apk'
            
            # Ensure downloads directory exists
            os.makedirs('downloads', exist_ok=True)
            
            filepath = os.path.join('downloads', filename)
            
            # Stream download to handle large files
            response = self.session.get(url, stream=True, timeout=60)
            response.raise_for_status()
            
            # Check if it's actually an APK file
            content_type = response.headers.get('content-type', '').lower()
            content_length = response.headers.get('content-length', 0)
            
            print(f"📊 Response - Type: {content_type}, Size: {content_length} bytes")
            
            # Write file in chunks
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            # Verify the downloaded file
            file_size = os.path.getsize(filepath)
            print(f"✅ Downloaded: {filepath} ({file_size} bytes)")
            
            # Check if file is actually an APK (APK files start with PK header)
            with open(filepath, 'rb') as f:
                header = f.read(4)
                if header == b'PK\x03\x04':
                    print("🔍 File verified as valid APK (PK header found)")
                else:
                    print("⚠️  Warning: File doesn't appear to be a valid APK")
            
            return filepath
            
        except Exception as e:
            print(f"❌ Error downloading APK: {e}")
            return None
    
    def upload_to_release(self, repo_name, filepath, release_tag, version):
        """Upload APK to GitHub release with proper error handling"""
        if not self.gh:
            print("❌ GitHub token not provided - cannot upload to releases")
            return False
        
        try:
            repo = self.gh.get_repo(repo_name)
            print(f"📦 Preparing to upload to repository: {repo_name}")
            
            # Check if file exists and has reasonable size
            if not os.path.exists(filepath):
                print(f"❌ File not found: {filepath}")
                return False
            
            file_size = os.path.getsize(filepath)
            if file_size < 1024:  # Less than 1KB
                print(f"❌ File too small: {file_size} bytes - likely not a valid APK")
                return False
            
            print(f"📁 File to upload: {filepath} ({file_size} bytes)")
            
            # Check if release exists
            try:
                release = repo.get_release(release_tag)
                print(f"🔄 Release '{release_tag}' exists, updating...")
                
                # Delete existing assets
                assets = release.get_assets()
                asset_count = 0
                for asset in assets:
                    print(f"🗑️  Deleting old asset: {asset.name}")
                    asset.delete_asset()
                    asset_count += 1
                print(f"✅ Deleted {asset_count} old assets")
                
            except Exception as e:
                print(f"📝 Release '{release_tag}' doesn't exist, creating new release...")
                # Create new release
                release = repo.create_git_release(
                    tag=release_tag,
                    name=f"{os.path.basename(filepath).replace('.apk', '')} {version}",
                    message=f"Auto-updated APK - Version {version}\n\nDownloaded from GetModsApk",
                    draft=False,
                    prerelease=False
                )
                print(f"✅ Created new release: {release_tag}")
            
            # Upload APK file
            print(f"⬆️  Uploading {os.path.basename(filepath)} to release...")
            with open(filepath, 'rb') as f:
                release.upload_asset(
                    path=filepath,
                    label=os.path.basename(filepath),
                    content_type='application/vnd.android.package-archive'
                )
            
            print(f"✅ Successfully uploaded {filepath} to release {release_tag}")
            return True
            
        except Exception as e:
            print(f"❌ Error uploading to release: {e}")
            return False
    
    def update_apk_list(self, apk_name, new_version):
        """Update APK list with new version"""
        try:
            config = load_config()
            updated = False
            
            for apk in config['tracked_apks']:
                if apk['name'] == apk_name:
                    print(f"📝 Updating {apk_name} from {apk['current_version']} to {new_version}")
                    apk['current_version'] = new_version
                    updated = True
                    break
            
            if updated:
                save_config(config)
                print(f"✅ Updated config for {apk_name}")
            else:
                print(f"❌ Could not find {apk_name} in config")
                
        except Exception as e:
            print(f"❌ Error updating APK list: {e}")
