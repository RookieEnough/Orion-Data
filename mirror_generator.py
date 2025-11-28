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

def clean_repo_url(url):
    """Normalizes 'https://github.com/User/Repo' to 'User/Repo'"""
    if not url: return None
    return url.replace("https://github.com/", "").replace("http://github.com/", "").strip().rstrip("/")

def fetch_github_data(clean_repo, strategy="list"):
    # Select Endpoint based on strategy
    if strategy == "latest":
        api_url = f"https://api.github.com/repos/{clean_repo}/releases/latest"
        print(f"⬇️  Fetching [LATEST]: {clean_repo}...")
    else:
        # Fetch 20 items to ensure we find older apps sharing the repo
        api_url = f"https://api.github.com/repos/{clean_repo}/releases?per_page=20"
        print(f"📚 Fetching [HISTORY]: {clean_repo}...")
    
    req = urllib.request.Request(api_url)
    
    # Auth (Crucial for rate limits)
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
        req.add_header('User-Agent', 'OrionStore-MirrorBot')
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            
            # NORMALIZATION RULE:
            # If strategy is 'list', data is already [ ... ].
            # If strategy is 'latest', data is { ... }.
            # We ALWAYS want to store a List [ ... ] in mirror.json so the frontend 
            # can consistently iterate over it.
            if isinstance(data, dict):
                return [data]
            return data
            
    except urllib.error.HTTPError as e:
        print(f"❌ Failed to fetch {clean_repo}: {e.code} ({e.reason})")
        return None
    except Exception as e:
        print(f"❌ Error {clean_repo}: {e}")
        return None

def main():
    print("--- Starting Mirror Generator ---")
    apps = get_apps()
    
    # 1. Determine Strategy per Repo (Standardized)
    repo_strategies = {} # Key: User/Repo, Value: 'latest' or 'list'

    print(f"🔍 Scanning {len(apps)} apps from apps.json...")

    for app in apps:
        raw_repo = app.get('githubRepo')
        if not raw_repo:
            continue
            
        repo = clean_repo_url(raw_repo)
        keyword = app.get('releaseKeyword')
        
        # Default to latest if not seen yet
        if repo not in repo_strategies:
            repo_strategies[repo] = "latest"
            
        # If ANY app in this repo has a keyword, force the whole repo to 'list' mode
        if keyword and str(keyword).strip():
            if repo_strategies[repo] == "latest":
                print(f"   -> 🔄 Upgrading {repo} to HISTORY mode (Reason: found keyword '{keyword}')")
            repo_strategies[repo] = "list"

    # 2. Fetch Data
    mirror_data = {}
    print("\n--- Fetching Data ---")
    
    for repo, strategy in repo_strategies.items():
        data = fetch_github_data(repo, strategy)
        if data:
            mirror_data[repo] = data

    # 3. Save Mirror
    with open(MIRROR_JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(mirror_data, f, indent=2)
    
    print(f"\n✅ Successfully mirrored {len(mirror_data)} repositories to {MIRROR_JSON_FILE}")

if __name__ == "__main__":
    main()
