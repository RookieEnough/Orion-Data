import json
import os
import requests
import sys

# Configuration
APPS_FILE = 'apps.json'
MIRROR_FILE = 'mirror.json'
GITHUB_TOKEN = os.environ.get('GH_TOKEN')

def load_apps():
    if not os.path.exists(APPS_FILE):
        print(f"Error: {APPS_FILE} not found.")
        return []
    with open(APPS_FILE, 'r') as f:
        return json.load(f)

def get_all_releases(repo_url):
    # Extract "owner/repo" from full URL if necessary
    if "github.com/" in repo_url:
        repo_slug = repo_url.split("github.com/")[-1].strip("/")
    else:
        repo_slug = repo_url

    # Fetch ALL releases, not just latest
    api_url = f"https://api.github.com/repos/{repo_slug}/releases?per_page=100"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Orion-Store-Bot"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    try:
        print(f"📦 Fetching ALL releases: {repo_slug}...")
        response = requests.get(api_url, headers=headers)
        if response.status_code == 200:
            releases = response.json()
            print(f"   ✅ Found {len(releases)} releases")
            
            # Debug: Show what we found
            total_assets = sum(len(release.get('assets', [])) for release in releases)
            print(f"   📊 Total assets: {total_assets}")
            
            # Show unique asset patterns
            asset_names = []
            for release in releases:
                for asset in release.get('assets', []):
                    asset_names.append(asset.get('name'))
            
            print(f"   📁 Sample assets: {asset_names[:5]}...")  # Show first 5
            
            return repo_slug, releases
        else:
            print(f"   ❌ Error {response.status_code}: {response.text}")
    except Exception as e:
        print(f"   ❌ Exception: {e}")
    
    return repo_slug, None

def main():
    apps = load_apps()
    mirror_data = {}
    
    # Use a set to avoid fetching the same repo multiple times
    processed_repos = set()

    # Repositories that we know contain multiple apps
    MULTI_APP_REPOS = [
        "RookieEnough/Orion-Data"
    ]

    for app in apps:
        repo = app.get('githubRepo')
        
        # Skip if no repo or already processed
        if not repo or repo in processed_repos:
            continue
        
        # For multi-app repos OR if app has releaseKeyword, fetch all releases
        needs_all_releases = any(multi_repo in repo for multi_repo in MULTI_APP_REPOS)
        
        if needs_all_releases:
            repo_slug, releases_data = get_all_releases(repo)
        else:
            # For single-app repos, you could use get_latest_release() here
            # But let's fetch all for consistency
            repo_slug, releases_data = get_all_releases(repo)
        
        if releases_data:
            # Store all releases, not just latest
            mirror_data[repo_slug] = releases_data
            
        processed_repos.add(repo)

    # Save to mirror.json
    with open(MIRROR_FILE, 'w') as f:
        json.dump(mirror_data, f, indent=2)
    
    print(f"\n✅ SUCCESS: Mirrored {len(mirror_data)} repositories to {MIRROR_FILE}")

if __name__ == "__main__":
    main()
