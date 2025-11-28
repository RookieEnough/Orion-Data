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

def fetch_latest_release(repo_input):
    # Normalize input to handle both "user/repo" and "https://github.com/user/repo"
    clean_repo = repo_input.replace("https://github.com/", "").rstrip("/")
    api_url = f"https://api.github.com/repos/{clean_repo}/releases/latest"
    
    print(f"Fetching: {clean_repo}...")
    
    req = urllib.request.Request(api_url)
    
    # Use Token if available (injected by GitHub Actions automatically)
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    
    try:
        with urllib.request.urlopen(req) as response:
            return clean_repo, json.loads(response.read())
    except urllib.error.HTTPError as e:
        print(f"Failed to fetch {clean_repo}: {e.code}")
        return clean_repo, None
    except Exception as e:
        print(f"Error {clean_repo}: {e}")
        return clean_repo, None

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
        key, data = fetch_latest_release(repo)
        if data:
            mirror_data[key] = data

    # 3. Save Mirror
    # We save it as a dictionary where Key = "user/repo" and Value = Release Object
    with open(MIRROR_JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(mirror_data, f, indent=2)
    
    print(f"Successfully mirrored {len(mirror_data)} repositories to {MIRROR_JSON_FILE}")

if __name__ == "__main__":
    main()
