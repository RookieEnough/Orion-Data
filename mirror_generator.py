import json
import os
import urllib.request
import urllib.error

# Configuration
APPS_JSON_FILE = 'apps.json'
MIRROR_JSON_FILE = 'mirror.json'

def get_apps():
    if not os.path.exists(APPS_JSON_FILE):
        print(f"Error: {APPS_JSON_FILE} not found.")
        return []
    with open(APPS_JSON_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def fetch_github_releases(repo):
    """Fetch ALL releases for a repository"""
    # Normalize repo name
    clean_repo = repo.replace("https://github.com/", "").rstrip("/")
    
    # ALWAYS fetch full history for these repos
    api_url = f"https://api.github.com/repos/{clean_repo}/releases?per_page=100"
    print(f"📦 Fetching ALL releases: {clean_repo}...")
    
    req = urllib.request.Request(api_url)
    
    # Auth
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    
    # Add User-Agent header
    req.add_header('User-Agent', 'Mozilla/5.0')
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            print(f"   ✅ Found {len(data)} releases")
            return clean_repo, data
    except Exception as e:
        print(f"   ❌ Failed: {e}")
        return clean_repo, []

def main():
    apps = get_apps()
    
    # List of repositories that need FULL history scanning
    # Add any multi-app repositories here
    FULL_HISTORY_REPOS = [
        "RookieEnough/Orion-Data"
        # Add more as needed
    ]
    
    # Get unique repositories from apps.json
    all_repos = set()
    for app in apps:
        repo = app.get('githubRepo')
        if repo:
            all_repos.add(repo)
    
    print(f"Found {len(all_repos)} repositories in apps.json")
    
    # Fetch data for each repository
    mirror_data = {}
    for repo in all_repos:
        # Check if this repo needs full history
        needs_full_history = any(full_repo in repo for full_repo in FULL_HISTORY_REPOS)
        
        if needs_full_history:
            key, data = fetch_github_releases(repo)
        else:
            # For single-app repos, use latest only (optional optimization)
            key, data = fetch_github_releases(repo)  # Still fetch all for now
        
        if data:
            mirror_data[key] = data
            
            # Debug: Show what we found
            total_assets = sum(len(release.get('assets', [])) for release in data)
            print(f"   📊 Total assets: {total_assets}")
            
            # Show asset names from all releases
            asset_names = []
            for release in data:
                for asset in release.get('assets', []):
                    asset_names.append(asset.get('name'))
            
            print(f"   📁 Assets found: {asset_names}")

    # Save to mirror.json
    with open(MIRROR_JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(mirror_data, f, indent=2)
    
    print(f"\n✅ SUCCESS: Mirrored {len(mirror_data)} repositories")
    print(f"📁 Output: {MIRROR_JSON_FILE}")

if __name__ == "__main__":
    main()
