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

def fetch_github_data(repo_input, strategy="list"):
    # Normalize input
    clean_repo = repo_input.replace("https://github.com/", "").rstrip("/")
    
    # Select Endpoint based on strategy
    if strategy == "latest":
        api_url = f"https://api.github.com/repos/{clean_repo}/releases/latest"
        print(f"Fetching [LATEST]: {clean_repo}...")
    else:
        # Default to fetching a list of 20 to catch multiple apps in monorepos
        api_url = f"https://api.github.com/repos/{clean_repo}/releases?per_page=20"
        print(f"Fetching [HISTORY]: {clean_repo}...")
    
    req = urllib.request.Request(api_url)
    
    # Auth
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            
            # Normalize to list for consistency in storage
            if isinstance(data, dict):
                return clean_repo, [data]
            return clean_repo, data
            
    except urllib.error.HTTPError as e:
        print(f"Failed to fetch {clean_repo}: {e.code} ({e.reason})")
        return clean_repo, []
    except Exception as e:
        print(f"Error {clean_repo}: {e}")
        return clean_repo, []

def main():
    apps = get_apps()
    
    # 1. Determine Strategy per Repo
    # Logic: If ANY app using a repo defines a 'releaseKeyword', we must fetch the list (history)
    # to find that specific app. If NO app defines a keyword, we assume it's a single-app repo
    # and we just want the absolute latest release.
    repo_strategies = {}

    for app in apps:
        repo = app.get('githubRepo')
        if not repo:
            continue
            
        keyword = app.get('releaseKeyword')
        
        # If we haven't seen this repo, default to 'latest'
        if repo not in repo_strategies:
            repo_strategies[repo] = "latest"
            
        # If we see a keyword, upgrade this repo to 'list' strategy
        # This ensures monorepos (like Orion-Data) always get full history scanned
        if keyword and len(keyword.strip()) > 0:
            repo_strategies[repo] = "list"

    # 2. Fetch Data
    mirror_data = {}
    for repo, strategy in repo_strategies.items():
        key, data = fetch_github_data(repo, strategy)
        if data:
            mirror_data[key] = data

    # 3. Save Mirror
    with open(MIRROR_JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(mirror_data, f, indent=2)
    
    print(f"Successfully mirrored {len(mirror_data)} repositories to {MIRROR_JSON_FILE}")

if __name__ == "__main__":
    main()
