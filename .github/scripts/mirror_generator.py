
import json
import requests
import os
import urllib.parse # Required for GitLab path encoding

# Files are accessed from the project root in the workflow
APPS_FILE = "apps.json"
MIRROR_FILE = "mirror.json"

def generate_mirror():
    mirror_data = {}
    
    # Setup GitHub Headers
    gh_headers = {}
    if os.environ.get("GH_TOKEN"):
        gh_headers["Authorization"] = f"Bearer {os.environ.get('GH_TOKEN')}"
    gh_headers["User-Agent"] = "OrionStore-MirrorBot"

    if not os.path.exists(APPS_FILE):
        print(f"Warning: {APPS_FILE} not found.")
        return

    try:
        with open(APPS_FILE, "r", encoding="utf-8") as f:
            apps = json.load(f)
    except Exception as e:
        print(f"Error reading apps.json: {e}")
        return

    # Deduplication Sets
    processed_github = set()
    processed_gitlab = set()

    print(f"🔍 Scanning {len(apps)} apps for repositories...")

    for app in apps:
        # ---------------------------
        # GITHUB HANDLER
        # ---------------------------
        if app.get("githubRepo"):
            # Clean the URL/String
            clean_gh = app["githubRepo"]\
                .replace("https://github.com/", "")\
                .replace("http://github.com/", "")\
                .replace("https://www.github.com/", "")\
                .strip("/")
            
            if clean_gh.lower() not in processed_github:
                print(f"--------------------------------")
                print(f"📥 Fetching GitHub: {clean_gh}")
                try:
                    url = f"https://api.github.com/repos/{clean_gh}/releases?per_page=50"
                    r = requests.get(url, headers=gh_headers)
                    
                    if r.status_code == 200:
                        mirror_data[clean_gh] = r.json() # Save using clean name (Owner/Repo)
                        processed_github.add(clean_gh.lower())
                        print(f"✅ Saved {len(r.json())} releases.")
                    elif r.status_code == 404:
                        print(f"❌ Repo not found.")
                    elif r.status_code == 403:
                        print(f"⚠️ Rate limit exceeded.")
                    else:
                        print(f"⚠️ Failed: {r.status_code}")
                except Exception as e:
                    print(f"❌ Error: {e}")

        # ---------------------------
        # GITLAB HANDLER
        # ---------------------------
        if app.get("gitlabRepo"):
            # GitLab uses URL Encoded paths (e.g. user%2Frepo)
            raw_path = app["gitlabRepo"].strip("/")
            
            # Check for custom domain (Obtainium style)
            domain = app.get("gitlabDomain", "gitlab.com")
            
            # Unique key for deduplication
            unique_key = f"{domain}::{raw_path}".lower()

            if unique_key not in processed_gitlab:
                print(f"--------------------------------")
                print(f"🦊 Fetching GitLab: {raw_path} ({domain})")
                
                try:
                    # Encode path: pixeldroid/bunny -> pixeldroid%2Fbunny
                    encoded_path = urllib.parse.quote(raw_path, safe='')
                    
                    # API: https://gitlab.example.com/api/v4/projects/{id}/releases
                    url = f"https://{domain}/api/v4/projects/{encoded_path}/releases"
                    
                    # Note: GitLab usually doesn't require auth for public repos, 
                    # but might rate limit on gitlab.com.
                    r = requests.get(url, timeout=20)
                    
                    if r.status_code == 200:
                        # IMPORTANT: Frontend expects data keyed by the repo path string
                        mirror_data[raw_path] = r.json() 
                        processed_gitlab.add(unique_key)
                        print(f"✅ Saved {len(r.json())} releases.")
                    else:
                        print(f"❌ Failed: {r.status_code} - {r.text[:50]}")
                except Exception as e:
                    print(f"❌ Error: {e}")

    # Write result
    try:
        with open(MIRROR_FILE, "w", encoding="utf-8") as f:
            json.dump(mirror_data, f, indent=2)
        print("--------------------------------")
        print(f"🎉 Success! {MIRROR_FILE} generated with {len(mirror_data)} repos.")
    except Exception as e:
        print(f"❌ Error writing file: {e}")

if __name__ == "__main__":
    generate_mirror()
