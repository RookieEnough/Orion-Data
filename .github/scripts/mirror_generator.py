import json
import os
import urllib.request
import urllib.error
import time

# Configuration
APPS_JSON_FILE = 'apps.json'
MIRROR_JSON_FILE = 'mirror.json'

def get_apps():
    if not os.path.exists(APPS_JSON_FILE):
        print(f"Error: {APPS_JSON_FILE} not found.")
        return []
    with open(APPS_JSON_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def fetch_latest_releases(repo_input):
    # Normalize input to handle both "user/repo" and "https://github.com/user/repo"
    # Removes trailing slashes and the domain if present
    clean_repo = repo_input.replace("https://github.com/", "").rstrip("/")
    
    # URL to fetch the last 10 releases
    api_url = f"https://api.github.com/repos/{clean_repo}/releases?per_page=10"
    
    print(f"Fetching releases for: {clean_repo}...")
    
    req = urllib.request.Request(api_url)
    
    # Use Token if available (injected by GitHub Actions)
    # Supports both standard GITHUB_TOKEN and custom GH_TOKEN
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    
    # User Agent is often required by GitHub API
    req.add_header('User-Agent', 'OrionStore-MirrorBot')
    
    try:
        with urllib.request.urlopen(req) as response:
            # Returns a LIST of release objects (Index 0 is latest)
            return clean_repo, json.loads(response.read())
    except urllib.error.HTTPError as e:
        print(f"Failed to fetch {clean_repo}: {e.code} {e.reason}")
        return clean_repo, None
    except Exception as e:
        print(f"Error fetching {clean_repo}: {e}")
        return clean_repo, None

def main():
    apps = get_apps()
    unique_repos = set()

    # 1. Identify unique repos from apps.json
    for app in apps:
        if app.get('githubRepo'):
            repo = app['githubRepo'].strip()
            if repo:
                unique_repos.add(repo)

    print(f"Found {len(unique_repos)} unique repositories to mirror.")

    # 2. Fetch Data
    mirror_data = {}
    for repo in unique_repos:
        key, data = fetch_latest_releases(repo)
        
        # We store the repo name (without https://github.com/) as the key
        # The data is now a LIST of releases, not just one object
        if data:
            mirror_data[key] = data
            print(f"✅ Success: {key}")
        
        # Sleep briefly to be nice to API if running locally without token
        if not os.environ.get('GH_TOKEN') and not os.environ.get('GITHUB_TOKEN'):
            time.sleep(1)

    # 3. Save Mirror
    try:
        with open(MIRROR_JSON_FILE, 'w', encoding='utf-8') as f:
            json.dump(mirror_data, f, indent=2)
        print(f"Successfully mirrored {len(mirror_data)} repositories to {MIRROR_JSON_FILE}")
    except Exception as e:
        print(f"Error saving mirror.json: {e}")

if __name__ == "__main__":
    main()
