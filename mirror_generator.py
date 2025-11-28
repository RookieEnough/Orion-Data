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

def fetch_recent_releases(repo_input):
    # Normalize input to handle both "user/repo" and "https://github.com/user/repo"
    clean_repo = repo_input.replace("https://github.com/", "").rstrip("/")
    
    # CHANGE: Fetch the list of releases (last 20) instead of just 'latest'
    # This ensures that if you host multiple apps in one repo, the store can find 
    # the older releases matching the specific app keyword.
    api_url = f"https://api.github.com/repos/{clean_repo}/releases?per_page=20"
    
    print(f"Fetching history for: {clean_repo}...")
    
    req = urllib.request.Request(api_url)
    
    # Use Token if available (injected by GitHub Actions automatically)
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            # Ensure we return a list. The /releases endpoint returns a list, 
            # but we double check just in case.
            if isinstance(data, list):
                return clean_repo, data
            else:
                return clean_repo, [data]
    except urllib.error.HTTPError as e:
        print(f"Failed to fetch {clean_repo}: {e.code}")
        return clean_repo, []
    except Exception as e:
        print(f"Error {clean_repo}: {e}")
        return clean_repo, []

def main():
    apps = get_apps()
    unique_repos = set()

    # 1. Identify unique repos from apps.json
    for app in apps:
        if app.get('githubRepo'):
            unique_repos.add(app['githubRepo'])

    # 2. Fetch Data
    mirror_data = {}
    for repo in unique_repos:
        key, data = fetch_recent_releases(repo)
        if data:
            mirror_data[key] = data

    # 3. Save Mirror
    # Structure: Key = "user/repo", Value = [ReleaseObject1, ReleaseObject2, ...]
    with open(MIRROR_JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(mirror_data, f, indent=2)
    
    print(f"Successfully mirrored {len(mirror_data)} repositories to {MIRROR_JSON_FILE}")

if __name__ == "__main__":
    main()
