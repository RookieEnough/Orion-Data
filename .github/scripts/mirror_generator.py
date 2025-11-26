import json
import os
import requests
import sys

# Configuration
APPS_FILE = 'apps.json'
MIRROR_FILE = 'mirror.json'
GITHUB_TOKEN = os.environ.get('GH_TOKEN')

def load_apps():
    if not os.path.exists(APPS_FILE):
        print(f"Error: {APPS_FILE} not found.")
        return []
    with open(APPS_FILE, 'r') as f:
        return json.load(f)

def get_latest_release(repo_url):
    # Extract "owner/repo" from full URL if necessary
    if "github.com/" in repo_url:
        repo_slug = repo_url.split("github.com/")[-1].strip("/")
    else:
        repo_slug = repo_url

    api_url = f"https://api.github.com/repos/{repo_slug}/releases/latest"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Orion-Store-Bot"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    try:
        print(f"Fetching: {repo_slug}...")
        response = requests.get(api_url, headers=headers)
        if response.status_code == 200:
            return repo_slug, response.json()
        elif response.status_code == 404:
            print(f"  - Release not found for {repo_slug}")
        else:
            print(f"  - Error {response.status_code}: {response.text}")
    except Exception as e:
        print(f"  - Exception: {e}")
    
    return repo_slug, None

def main():
    apps = load_apps()
    mirror_data = {}
    
    # Use a set to avoid fetching the same repo multiple times
    processed_repos = set()

    for app in apps:
        repo = app.get('githubRepo')
        
        # Skip if no repo or already processed
        if not repo or repo in processed_repos:
            continue
            
        repo_slug, release_data = get_latest_release(repo)
        
        if release_data:
            # We Key the mirror.json by the repo slug (e.g., "RookieEnough/Revanced-Orion")
            mirror_data[repo_slug] = release_data
            
        processed_repos.add(repo)

    # Save to mirror.json
    with open(MIRROR_FILE, 'w') as f:
        json.dump(mirror_data, f, indent=2)
    
    print(f"\nSuccessfully mirrored {len(mirror_data)} repositories to {MIRROR_FILE}")

if __name__ == "__main__":
    main()
