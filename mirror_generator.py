import json
import os
import urllib.request
import urllib.error
import re

# Configuration
APPS_JSON_FILE = 'apps.json'
MIRROR_JSON_FILE = 'mirror.json'
MANIFEST_FILENAME = 'repo-manifest.json'

def get_apps():
    if not os.path.exists(APPS_JSON_FILE):
        print(f"Error: {APPS_JSON_FILE} not found.")
        return []
    with open(APPS_JSON_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def fetch_url(url):
    """Generic URL fetcher with error handling"""
    req = urllib.request.Request(url)
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    req.add_header('User-Agent', 'Mozilla/5.0')
    
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read())
    except Exception as e:
        print(f"Failed to fetch {url}: {e}")
        return None

def fetch_repo_manifest(repo):
    """Fetch repo-manifest.json from repository"""
    manifest_url = f"https://raw.githubusercontent.com/{repo}/main/{MANIFEST_FILENAME}"
    return fetch_url(manifest_url)

def detect_repo_type(repo, releases):
    """Detect if repo is single-app or multi-app"""
    # Try to fetch manifest first
    manifest = fetch_repo_manifest(repo)
    if manifest and manifest.get('repoType') == 'multi-app':
        return 'multi-app-with-manifest', manifest
    
    # Fallback: analyze release assets
    unique_apps = set()
    for release in releases[:5]:  # Check first 5 releases
        for asset in release.get('assets', []):
            asset_name = asset.get('name', '').lower()
            # Simple heuristic: if we see multiple distinct app patterns
            if 'capcut' in asset_name:
                unique_apps.add('capcut')
            if 'mx' in asset_name or 'mx-pro' in asset_name:
                unique_apps.add('mx-player')
            if 'revanced' in asset_name:
                unique_apps.add('revanced')
    
    if len(unique_apps) > 1:
        return 'multi-app-detected', None
    else:
        return 'single-app', None

def fetch_github_data(repo):
    """Fetch all releases for a repository"""
    api_url = f"https://api.github.com/repos/{repo}/releases?per_page=50"
    print(f"📦 Fetching: {repo}...")
    
    data = fetch_url(api_url)
    if not data:
        return repo, []
    
    # Detect repository type
    repo_type, manifest = detect_repo_type(repo, data)
    print(f"   Type: {repo_type}")
    
    # Add metadata to the repository data
    enhanced_data = {
        'repo_type': repo_type,
        'manifest': manifest,
        'releases': data
    }
    
    return repo, enhanced_data

def find_app_in_releases(app_id, release_keyword, repo_data):
    """Find an app in repository releases using various strategies"""
    releases = repo_data.get('releases', [])
    manifest = repo_data.get('manifest')
    
    # Strategy 1: Use manifest if available
    if manifest:
        for app_def in manifest.get('apps', []):
            if app_def.get('id') == app_id:
                asset_pattern = app_def.get('assetPattern')
                return find_by_pattern(releases, asset_pattern)
    
    # Strategy 2: Use releaseKeyword from apps.json
    if release_keyword:
        return find_by_keyword(releases, release_keyword)
    
    # Strategy 3: Fallback - use app_id
    return find_by_keyword(releases, app_id)

def find_by_pattern(releases, pattern):
    """Find assets matching a pattern"""
    for release in releases:
        for asset in release.get('assets', []):
            asset_name = asset.get('name', '')
            if fnmatch(asset_name.lower(), pattern.lower()):
                return asset
    return None

def find_by_keyword(releases, keyword):
    """Find assets containing keyword"""
    for release in releases:
        for asset in release.get('assets', []):
            asset_name = asset.get('name', '')
            if keyword.lower() in asset_name.lower():
                return asset
    return None

def fnmatch(name, pattern):
    """Simple pattern matching (like *.apk)"""
    pattern = pattern.replace('*', '.*')
    return re.match(pattern, name, re.IGNORECASE) is not None

def main():
    apps = get_apps()
    
    # Group apps by repository
    repo_apps = {}
    for app in apps:
        repo = app.get('githubRepo')
        if repo:
            if repo not in repo_apps:
                repo_apps[repo] = []
            repo_apps[repo].append(app)
    
    # Fetch data for each repository
    mirror_data = {}
    for repo, repo_apps_list in repo_apps.items():
        key, data = fetch_github_data(repo)
        if data:
            mirror_data[key] = data
    
    # Save enhanced mirror data
    with open(MIRROR_JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(mirror_data, f, indent=2)
    
    print(f"\n✅ Successfully mirrored {len(mirror_data)} repositories")
    
    # Test: Try to find each app
    print("\n=== APP DISCOVERY ===")
    for app in apps:
        repo = app.get('githubRepo')
        app_id = app.get('id')
        keyword = app.get('releaseKeyword', '')
        
        if repo in mirror_data:
            asset = find_app_in_releases(app_id, keyword, mirror_data[repo])
            status = "✅" if asset else "❌"
            asset_name = asset.get('name', 'Not found') if asset else 'Not found'
            print(f"{status} {app_id}: {asset_name}")

if __name__ == "__main__":
    main()
