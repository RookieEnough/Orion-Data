import json
import os
import urllib.request
import urllib.error
import time

# Configuration
APPS_JSON_FILE = 'apps.json'
MIRROR_JSON_FILE = 'mirror.json'

def get_apps():
    """Reads the applications list from apps.json."""
    if not os.path.exists(APPS_JSON_FILE):
        print(f"Error: {APPS_JSON_FILE} not found.")
        return []
    with open(APPS_JSON_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def fetch_latest_releases(repo_input):
    """
    Fetches the latest releases for a given GitHub repository using the GitHub API.
    
    Args:
        repo_input (str): The repository string (e.g., "User/Repo" or "https://github.com/User/Repo").
        
    Returns:
        tuple: (clean_repo, data) where clean_repo is the key for mirror.json,
               and data is the list of release objects, or None on failure.
    """
    # Normalize input to handle both "user/repo" and "https://github.com/user/repo"
    # Removes trailing slashes and the domain if present.
    clean_repo = repo_input.replace("https://github.com/", "").rstrip("/")
    
    # URL to fetch the last 10 releases
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
        return clean_repo, data
    except urllib.error.HTTPError as e:
        print(f"Failed to fetch {clean_repo}: {e.code} {e.reason}")
        return clean_repo, None
    except Exception as e:
        print(f"Error fetching {clean_repo}: {e}")
        return clean_repo, None

def main():
    apps = get_apps()
    
    # V4 CHANGE: Use a dictionary to enforce case-insensitive uniqueness.
    # The key is the lowercased, normalized repo name. The value is the 
    # original (preferred) casing/format for the fetch URL and final key.
    normalized_repos_map = {}

    # 1. Identify unique repos from apps.json (Consolidated/Normalized)
    for app in apps:
        repo = app.get('githubRepo')
        if repo:
            repo_strip = repo.strip()
            if repo_strip:
                # Create a canonical, lowercase, clean key for uniqueness check
                normalized_key = repo_strip.lower().replace("https://github.com/", "").rstrip("/")
                
                # Store the original, clean key (repo_strip) using the normalized key
                # This ensures we use the correct case for fetching later.
                if normalized_key not in normalized_repos_map:
                    normalized_repos_map[normalized_key] = repo_strip
    
    # Convert the unique, case-insensitive values back into the set to be processed
    unique_repos = set(normalized_repos_map.values())
    
    print(f"Found {len(unique_repos)} unique repositories to mirror.")

    # 2. Fetch Data
    mirror_data = {}
    for repo in unique_repos:
        # repo here is the original, preferred, unique-by-casefold string
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
        print(f"\nSuccessfully saved mirror data to {MIRROR_JSON_FILE}")
    except Exception as e:
        print(f"Error saving file: {e}")

if __name__ == '__main__':
    main()
