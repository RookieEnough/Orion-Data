import json
import os
import requests
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
    Always returns a LIST of releases.
    """
    token = os.environ.get('GITHUB_TOKEN')
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'OrionStore-MirrorBot'
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'
    
    try:
        if strategy == "latest":
            print(f"⬇️  Fetching [LATEST] for: {repo_slug}...")
            url = f"https://api.github.com/repos/{repo_slug}/releases/latest"
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                # Wrap single object in list
                return [response.json()]
            elif response.status_code == 404:
                print(f"   ⚠️ Repo or Release not found: {repo_slug}")
                return []
            else:
                print(f"   ❌ API Error {response.status_code}: {response.text}")
                return []
                
        else: # strategy == "list" (History)
            # CRITICAL FIX: per_page=100 ensures we see older releases in shared repos
            print(f"📚 Fetching [HISTORY - 100 items] for: {repo_slug}...")
            url = f"https://api.github.com/repos/{repo_slug}/releases?per_page=100"
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    print(f"   ✅ Retrieved {len(data)} releases.")
                    return data
                return []
            else:
                print(f"   ❌ API Error {response.status_code}: {response.text}")
                return []

    except Exception as e:
        print(f"   ❌ Network Error for {repo_slug}: {e}")
        return []

def main():
    print("--- Starting Deep-Dive Mirror Generator ---")
    apps = get_apps()
    
    if not apps:
        print("No apps found.")
        return

    # 1. Analyze Apps & Determine Strategy per Repo
    repo_strategies = {} 
    repo_display_names = {}

    print(f"🔍 Scanning {len(apps)} apps configuration...")

    for app in apps:
        raw_repo = app.get('githubRepo')
        if not raw_repo:
            continue
            
        norm_key = normalize_repo(raw_repo)
        
        # Store display name (User/Repo)
        if norm_key not in repo_display_names:
            clean_display = raw_repo.replace("https://github.com/", "").replace("http://github.com/", "").strip().rstrip("/")
            repo_display_names[norm_key] = clean_display

        # Check keyword
        keyword = app.get('releaseKeyword')
        has_keyword = bool(keyword and str(keyword).strip())

        # Strategy Logic:
        # If ANY app in this repo uses a keyword, we must fetch the full LIST (history).
        # Otherwise, we can just fetch LATEST to save API calls.
        current_strategy = repo_strategies.get(norm_key, 'latest')
        
        if has_keyword:
            if current_strategy == 'latest' and norm_key in repo_strategies:
                 print(f"   🔄 Upgrading {norm_key} to HISTORY mode (shared repo needs keyword search)")
            repo_strategies[norm_key] = 'list'
        else:
            if norm_key not in repo_strategies:
                repo_strategies[norm_key] = 'latest'

    # 2. Fetch Data
    mirror_output = {}
    print(f"\n--- Processing {len(repo_strategies)} unique repositories ---")

    for norm_key, strategy in repo_strategies.items():
        display_name = repo_display_names[norm_key]
        data = fetch_github_data(display_name, strategy)
        
        if data:
            mirror_output[display_name] = data
        
        # Slight delay to be nice to API
        time.sleep(0.5)

    # 3. Save
    print("\n--- Saving Data ---")
    try:
        with open(MIRROR_JSON_FILE, 'w', encoding='utf-8') as f:
            json.dump(mirror_output, f, indent=2)
        print(f"✅ Successfully wrote {len(mirror_output)} entries to {MIRROR_JSON_FILE}")
    except Exception as e:
        print(f"❌ Failed to write file: {e}")

if __name__ == "__main__":
    main()
