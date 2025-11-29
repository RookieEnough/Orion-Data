import json
import requests
import os

# Files are accessed from the project root in the workflow
APPS_FILE = "apps.json"
MIRROR_FILE = "mirror.json"

def get_repos_from_apps():
    """
    Parses apps.json to find all GitHub repos we need to mirror.
    Handles case-insensitive deduplication to prevent double fetching.
    """
    repos_map = {} # Stores normalized_key -> original_key
    
    if os.path.exists(APPS_FILE):
        try:
            with open(APPS_FILE, "r", encoding="utf-8") as f:
                apps = json.load(f)
                for app in apps:
                    if app.get("githubRepo"):
                        # Clean the URL/String
                        clean = app["githubRepo"]\
                            .replace("https://github.com/", "")\
                            .replace("http://github.com/", "")\
                            .replace("https://www.github.com/", "")\
                            .strip("/")
                        
                        # Use lowercase key for deduplication check
                        # Store original casing as the value
                        normalized = clean.lower()
                        if normalized not in repos_map:
                            repos_map[normalized] = clean
                            
        except Exception as e:
            print(f"Warning: Could not parse {APPS_FILE}: {e}")
    else:
        print(f"Warning: {APPS_FILE} not found.")
    
    # Return the list of original repo names
    return list(repos_map.values())

def generate_mirror():
    repos = get_repos_from_apps()
    mirror_data = {}
    
    headers = {}
    # Use the token from GitHub Actions for higher rate limits
    if os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ.get('GH_TOKEN')}"
    
    # GitHub API Best Practice: Always send a User-Agent
    headers["User-Agent"] = "OrionStore-MirrorBot"

    print(f"🔍 Found {len(repos)} unique repositories to mirror.")

    for repo in repos:
        print(f"--------------------------------")
        print(f"📥 Fetching releases for: {repo}")
        try:
            # 1. Fetch up to 100 releases to catch apps that aren't at the very top (Fixes CapCut issue)
            url = f"https://api.github.com/repos/{repo}/releases?per_page=100"
            r = requests.get(url, headers=headers)
            
            if r.status_code == 200:
                releases = r.json()
                
                # 2. CRITICAL FIX: Save the LIST of releases, not just the first one.
                # This ensures App.tsx can loop through 100 items to find the correct matching app.
                mirror_data[repo] = releases
                
                print(f"✅ Saved {len(releases)} releases.")
            elif r.status_code == 404:
                print(f"❌ Repo not found.")
            elif r.status_code == 403:
                print(f"⚠️ Rate limit exceeded.")
            else:
                print(f"⚠️ Failed with status: {r.status_code}")
                
        except Exception as e:
            print(f"❌ Error: {e}")

    # Write the new mirror.json
    try:
        with open(MIRROR_FILE, "w", encoding="utf-8") as f:
            json.dump(mirror_data, f, indent=2)
        print("--------------------------------")
        print(f"🎉 Success! {MIRROR_FILE} generated.")
    except Exception as e:
        print(f"❌ Error writing file: {e}")

if __name__ == "__main__":
    generate_mirror()
