import json
import os
import requests

# Configuration
APPS_JSON_FILE = 'apps.json'
MIRROR_JSON_FILE = 'mirror.json'

def get_apps():
    if not os.path.exists(APPS_JSON_FILE):
        print(f"Error: {APPS_JSON_FILE} not found.")
        return []
    with open(APPS_JSON_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def normalize_repo(url_or_name):
    """
    Normalizes a repo reference to 'user/repo' (lowercase).
    Handles:
    - https://github.com/User/Repo -> user/repo
    - User/Repo -> user/repo
    - user/repo -> user/repo
    """
    if not url_or_name:
        return None
    
    # Strip protocol and domain
    clean = url_or_name.replace("https://github.com/", "").replace("http://github.com/", "")
    
    # Remove trailing slashes and whitespace
    clean = clean.strip().rstrip("/")
    
    # Lowercase for consistent dictionary keys
    return clean.lower()

def fetch_github_data(repo_slug, strategy="list"):
    """
    Fetches release data from GitHub API.
    Always returns a LIST of releases, even if fetching 'latest'.
    """
    # Authorization
    token = os.environ.get('GITHUB_TOKEN')
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'OrionStore-MirrorBot'
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'
    
    try:
        if strategy == "latest":
            print(f"⬇️  Fetching [LATEST]: {repo_slug}...")
            url = f"https://api.github.com/repos/{repo_slug}/releases/latest"
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                # Wrap the single object in a list so frontend always gets an array
                return [response.json()]
            elif response.status_code == 404:
                print(f"   ⚠️ Repo or Release not found: {repo_slug}")
                return []
            else:
                print(f"   ❌ API Error {response.status_code}: {response.text}")
                return []
                
        else: # strategy == "list" (History)
            print(f"📚 Fetching [HISTORY]: {repo_slug}...")
            # Fetch last 10 releases to ensure we find the specific keyword
            url = f"https://api.github.com/repos/{repo_slug}/releases?per_page=10"
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    return data
                return []
            else:
                print(f"   ❌ API Error {response.status_code}: {response.text}")
                return []

    except Exception as e:
        print(f"   ❌ Network Error for {repo_slug}: {e}")
        return []

def main():
    print("--- Starting Robust Mirror Generator ---")
    apps = get_apps()
    
    if not apps:
        print("No apps found.")
        return

    # 1. Analyze Apps & Determine Strategy per Repo
    # We use a map: normalized_repo_key -> strategy
    repo_strategies = {} 
    # We also keep a map of original repo names to preserve casing for the output key
    repo_display_names = {}

    print(f"🔍 Scanning {len(apps)} apps configuration...")

    for app in apps:
        raw_repo = app.get('githubRepo')
        if not raw_repo:
            continue
            
        # Normalize: 'https://github.com/User/Repo' -> 'user/repo'
        norm_key = normalize_repo(raw_repo)
        
        # Store a display version (User/Repo without https://)
        if norm_key not in repo_display_names:
            clean_display = raw_repo.replace("https://github.com/", "").replace("http://github.com/", "").strip().rstrip("/")
            repo_display_names[norm_key] = clean_display

        # Check keyword
        keyword = app.get('releaseKeyword')
        has_keyword = bool(keyword and str(keyword).strip())

        # Logic:
        # If repo is new, default to 'latest'
        # If repo exists and already 'list', keep 'list'
        # If repo exists and is 'latest', but this app has keyword -> upgrade to 'list'
        
        current_strategy = repo_strategies.get(norm_key, 'latest')
        
        if has_keyword:
            if current_strategy == 'latest' and norm_key in repo_strategies:
                 print(f"   🔄 Upgrading {norm_key} to HISTORY mode (shared repo needs keyword search)")
            repo_strategies[norm_key] = 'list'
        else:
            if norm_key not in repo_strategies:
                repo_strategies[norm_key] = 'latest'

    # 2. Fetch Data based on determined strategies
    mirror_output = {}
    print(f"\n--- Processing {len(repo_strategies)} unique repositories ---")

    for norm_key, strategy in repo_strategies.items():
        # Recover a nice display name for the key (e.g. "RookieEnough/Orion-Data")
        display_name = repo_display_names[norm_key]
        
        data = fetch_github_data(display_name, strategy)
        
        if data:
            # Always store as the display name (User/Repo)
            mirror_output[display_name] = data

    # 3. Save to mirror.json
    print("\n--- Saving Data ---")
    try:
        with open(MIRROR_JSON_FILE, 'w', encoding='utf-8') as f:
            json.dump(mirror_output, f, indent=2)
        print(f"✅ Successfully wrote {len(mirror_output)} entries to {MIRROR_JSON_FILE}")
    except Exception as e:
        print(f"❌ Failed to write file: {e}")

if __name__ == "__main__":
    main()
