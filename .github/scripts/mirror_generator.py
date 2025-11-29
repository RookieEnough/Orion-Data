import json
import os
import urllib.request
import urllib.error
import time

# Configuration
APPS_JSON_FILE = 'apps.json'
MIRROR_JSON_FILE = 'mirror.json'

def get_apps():
    """Loads and returns the list of apps from the apps.json file."""
    if not os.path.exists(APPS_JSON_FILE):
        print(f"Error: {APPS_JSON_FILE} not found.")
        return []
    with open(APPS_JSON_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def normalize_repo(repo_input):
    """
    Normalizes the repository input string to a 'user/repo' format.
    Handles both "user/repo" and "https://github.com/user/repo".
    """
    if not repo_input:
        return None
    
    # Remove the GitHub domain and strip trailing slashes
    clean_repo = repo_input.replace("https://github.com/", "").rstrip("/")
    return clean_repo if clean_repo else None


def fetch_latest_releases(clean_repo):
    """
    Fetches the latest 10 releases for a given clean repository name.
    
    Returns:
        tuple: (clean_repo_name, list_of_releases) or (clean_repo_name, None) on failure.
    """
    
    # URL to fetch the last 10 releases
    # clean_repo is guaranteed to be in the format 'user/repo' now
    api_url = f"https://api.github.com/repos/{clean_repo}/releases?per_page=10"
    
    print(f"Fetching releases for: {clean_repo}...")
    
    req = urllib.request.Request(api_url)
    
    # Use Token if available (injected by GitHub Actions)
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    
    # User Agent is often required by GitHub API
    req.add_header('User-Agent', 'OrionStore-MirrorBot')
    
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            
            # The API returns a list of releases (can be empty: []).
            return clean_repo, data
            
    except urllib.error.HTTPError as e:
        print(f"Failed to fetch {clean_repo}: {e.code} {e.reason}")
        return clean_repo, None
    except Exception as e:
        print(f"Error fetching {clean_repo}: {e}")
        return clean_repo, None

def main():
    """Main function to generate the mirror.json file."""
    apps = get_apps()
    unique_repos = set()

    # 1. Identify unique, normalized repos from apps.json (FIXED)
    # By normalizing here, we prevent duplicate API calls and key inconsistency.
    for app in apps:
        if app.get('githubRepo'):
            repo = app['githubRepo'].strip()
            if repo:
                clean_repo = normalize_repo(repo)
                if clean_repo:
                    unique_repos.add(clean_repo)

    print(f"Found {len(unique_repos)} unique repositories to mirror.")

    # 2. Fetch Data
    mirror_data = {}
    for repo in unique_repos:
        # 'repo' here is already the cleaned, normalized name
        key, data = fetch_latest_releases(repo)
        
        # CRITICAL FIX: We must check if the fetch succeeded (data is not None), 
        # but we allow empty lists ([]) because a successful fetch might 
        # return zero releases. The old 'if data:' failed on empty lists.
        if data is not None:
            mirror_data[key] = data
            print(f"✅ Success: {key}")
        
        # Sleep briefly to be nice to API if running locally without token
        if not os.environ.get('GH_TOKEN') and not os.environ.get('GITHUB_TOKEN'):
            time.sleep(1)

    # 3. Save Mirror
    try:
        with open(MIRROR_JSON_FILE, 'w', encoding='utf-8') as f:
            json.dump(mirror_data, f, indent=2)
        print(f"\nSuccessfully saved release data for {len(mirror_data)} repos to {MIRROR_JSON_FILE}")
    except Exception as e:
        print(f"Error saving {MIRROR_JSON_FILE}: {e}")

if __name__ == '__main__':
    main()
