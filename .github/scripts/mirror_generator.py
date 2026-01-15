
import json
import requests
import os
import urllib.parse
import shutil
import msgpack

# Files
APPS_FILE = "apps.json"
MIRROR_FILE = "mirror.json"
MIRRORS_DIR = "mirrors"
BINARY_MANIFEST_FILE = "updates.bin"

def minify_release(release):
    """
    THIN MIRROR PROTOCOL
    --------------------
    Strips 90% of unused metadata (author info, node_ids, tree urls, etc.)
    Returns a lightweight dict compatible with the Orion Frontend schema.
    """
    if not isinstance(release, dict): return release
    
    # 1. Minify Assets
    minified_assets = []
    
    # GitHub Structure
    if 'assets' in release and isinstance(release['assets'], list):
        for asset in release['assets']:
            minified_assets.append({
                "name": asset.get("name"),
                "size": asset.get("size"),
                "browser_download_url": asset.get("browser_download_url"),
                "content_type": asset.get("content_type"),
                "download_count": asset.get("download_count")
            })
            
    # GitLab Structure (Standardize to match GitHub for Frontend compatibility)
    elif 'assets' in release and isinstance(release['assets'], dict):
        # Flatten GitLab links into the 'assets' array expected by frontend
        gl_links = release['assets'].get('links', [])
        for link in gl_links:
             minified_assets.append({
                "name": link.get("name"),
                "size": 0, # GitLab links often don't have size metadata available here
                "browser_download_url": link.get("url"),
                "link_type": link.get("link_type")
            })

    # 2. Return Minified Release
    return {
        "tag_name": release.get("tag_name"),
        "name": release.get("name"),
        "prerelease": release.get("prerelease", False), # Added for Version Selection Feature
        # Fallback for different date fields across APIs
        "published_at": release.get("published_at") or release.get("released_at") or release.get("created_at"),
        "html_url": release.get("html_url") or release.get("_links", {}).get("self"),
        "assets": minified_assets
    }

def generate_mirror():
    # 1. Setup & Cleanup
    print("🧹 Cleaning mirrors directory...")
    if os.path.exists(MIRRORS_DIR):
        shutil.rmtree(MIRRORS_DIR)
    os.makedirs(MIRRORS_DIR)

    gh_headers = {}
    if os.environ.get("GH_TOKEN"):
        gh_headers["Authorization"] = f"Bearer {os.environ.get('GH_TOKEN')}"
    gh_headers["User-Agent"] = "OrionStore-Nuclear/1.1"

    if not os.path.exists(APPS_FILE):
        print(f"❌ Error: {APPS_FILE} not found.")
        return

    try:
        with open(APPS_FILE, "r", encoding="utf-8") as f:
            apps = json.load(f)
    except Exception as e:
        print(f"❌ Error reading apps.json: {e}")
        return

    # 2. Fetch Data (Deduplicated)
    repo_cache = {} 
    unique_repos = set()
    app_to_repo_map = {} 

    print(f"🔍 Analyzing {len(apps)} apps for data sources...")

    for app in apps:
        repo_key = None
        source_type = None # 'github' or 'gitlab'
        domain = "gitlab.com"

        # Logic to determine Repo Key
        if app.get("githubRepo"):
            repo_key = app["githubRepo"].replace("https://github.com/", "").strip("/")
            source_type = 'github'
        elif app.get("repoUrl") and "github.com" in app["repoUrl"]:
            parts = app["repoUrl"].split("github.com/")
            if len(parts) > 1:
                repo_key = parts[1].split('/')[0] + "/" + parts[1].split('/')[1]
                repo_key = repo_key.replace(".git", "").strip("/")
                source_type = 'github'
        elif app.get("gitlabRepo"):
            repo_key = app["gitlabRepo"].strip("/")
            source_type = 'gitlab'
            domain = app.get("gitlabDomain", "gitlab.com")
        elif app.get("repoUrl") and "gitlab" in app["repoUrl"]:
            try:
                parsed = urllib.parse.urlparse(app["repoUrl"])
                path_parts = parsed.path.strip("/").split("/")
                if len(path_parts) >= 2:
                    repo_key = "/".join(path_parts)
                    source_type = 'gitlab'
                    domain = parsed.netloc
            except: pass

        if repo_key and source_type:
            unique_key = f"{source_type}::{domain}::{repo_key.lower()}"
            unique_repos.add((unique_key, repo_key, source_type, domain))
            app_to_repo_map[app['id']] = unique_key

    # 3. Fetching Phase
    print(f"📡 Detected {len(unique_repos)} unique repositories. Starting fetch & minify...")

    for u_key, repo_path, s_type, s_domain in unique_repos:
        if u_key in repo_cache: continue

        print(f"⬇️ Fetching {s_type.title()}: {repo_path}...")
        
        try:
            data = None
            if s_type == 'github':
                url = f"https://api.github.com/repos/{repo_path}/releases?per_page=20"
                r = requests.get(url, headers=gh_headers)
                if r.status_code == 200:
                    data = r.json()
                elif r.status_code == 404:
                    print(f"   ⚠️ Repo not found: {repo_path}")
                elif r.status_code == 403:
                    print(f"   ⚠️ Rate limit exceeded for {repo_path}")
            
            elif s_type == 'gitlab':
                encoded_path = urllib.parse.quote(repo_path, safe='')
                url = f"https://{s_domain}/api/v4/projects/{encoded_path}/releases"
                r = requests.get(url, timeout=20)
                if r.status_code == 200:
                    data = r.json()
                else:
                    print(f"   ⚠️ GitLab Error {r.status_code}: {repo_path}")

            if data:
                # APPLY THIN MIRROR PROTOCOL
                if isinstance(data, list):
                    minified_data = [minify_release(r) for r in data]
                else:
                    minified_data = minify_release(data)
                
                # Check if empty list returned (repo exists but no releases)
                if not minified_data:
                    print(f"   ⚠️ Repo exists but has NO RELEASES: {repo_path}")
                
                repo_cache[u_key] = minified_data
                repo_cache[repo_path] = minified_data 

        except Exception as e:
            print(f"   ❌ Network Error: {e}")

    # --- NEW: MISSING APPS AUDIT REPORT ---
    print("\n" + "="*50)
    print("🕵️  MISSING APPS AUDIT REPORT")
    print("="*50)
    
    missing_count = 0
    
    for app in apps:
        app_id = app.get('id', 'unknown')
        app_name = app.get('name', 'Unknown')
        unique_key = app_to_repo_map.get(app_id)
        
        status = "OK"
        reason = ""
        
        if not unique_key:
            status = "MISSING"
            reason = "Could not parse repoUrl or githubRepo from apps.json"
        elif unique_key not in repo_cache:
            status = "MISSING"
            reason = "API Request Failed (404 Not Found, 403 Rate Limit, or Network Error)"
        elif not repo_cache[unique_key]:
            status = "MISSING"
            reason = "Repo fetched successfully, but it has ZERO releases."
            
        if status == "MISSING":
            missing_count += 1
            print(f"❌ {app_name} (ID: {app_id})")
            print(f"   Reason: {reason}")
            print(f"   Target: {app.get('githubRepo') or app.get('repoUrl')}")
            print("-" * 30)

    if missing_count == 0:
        print("✅ PERFECT RUN! All apps accounted for.")
    else:
        print(f"\n⚠️  TOTAL MISSING: {missing_count}/{len(apps)}")
        print("   Apps listed above will display 'Varies' or 'Latest' in the store.")
        print("   Action: Check repo URLs, verify Releases exist, or check GitHub Status.")
    print("="*50 + "\n")
    # --------------------------------------

    # 4. Generate Monolithic File (Legacy)
    print("💾 Saving legacy mirror.json...")
    legacy_data = {k: v for k, v in repo_cache.items() if "::" not in k and v} 
    try:
        with open(MIRROR_FILE, "w", encoding="utf-8") as f:
            json.dump(legacy_data, f, indent=None, separators=(',', ':'))
    except Exception as e:
        print(f"❌ Error writing mirror.json: {e}")

    # 5. Generate Atomic Shards
    print("⚛️ Generating Atomic Shards...")
    shard_count = 0
    
    # 6. Generate Binary Manifest (The Nuclear Option)
    print("☢️ Generating Binary Manifest...")
    manifest = {} # Map: AppID -> Version
    
    for app in apps:
        app_id = app.get('id')
        
        # --- Shard Generation Logic ---
        unique_key = app_to_repo_map.get(app_id)
        
        # Determine latest version for Manifest
        # Priority: Live Data > Config Data > Fallback
        live_version = None
        if unique_key and unique_key in repo_cache and repo_cache[unique_key]:
            cached_data = repo_cache[unique_key]
            # Write Shard
            identifier = app.get('packageName') or app.get('id')
            if identifier:
                identifier = identifier.lower().strip()
                safe_name = "".join([c for c in identifier if c.isalnum() or c in "._-"])
                char1 = safe_name[0] if len(safe_name) > 0 else "_"
                char2 = safe_name[1] if len(safe_name) > 1 else "_"
                
                target_dir = os.path.join(MIRRORS_DIR, char1, char2)
                os.makedirs(target_dir, exist_ok=True)
                target_file = os.path.join(target_dir, f"{safe_name}.json")
                
                try:
                    with open(target_file, "w", encoding="utf-8") as f:
                        json.dump(cached_data, f, separators=(',', ':'))
                    shard_count += 1
                except: pass

            # Extract Version for Manifest
            if isinstance(cached_data, list) and len(cached_data) > 0:
                live_version = cached_data[0].get('tag_name')
            elif isinstance(cached_data, dict):
                live_version = cached_data.get('tag_name')

        # Fallback to apps.json version if live fetch failed
        final_version = live_version if live_version else app.get('version', 'Latest')
        
        if app_id:
            manifest[app_id] = final_version

    # Write Binary Manifest
    try:
        with open(BINARY_MANIFEST_FILE, "wb") as f:
            f.write(msgpack.packb(manifest))
        print(f"   ✅ Saved {BINARY_MANIFEST_FILE} ({len(manifest)} entries)")
    except Exception as e:
        print(f"   ❌ Failed to write binary manifest: {e}")

    print("--------------------------------")
    print(f"🎉 Success! Generated {shard_count} thin shards + 1 binary manifest.")

if __name__ == "__main__":
    generate_mirror()
