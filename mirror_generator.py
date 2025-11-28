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
        # For multi-app repos, fetch more releases to find all apps
        api_url = f"https://api.github.com/repos/{clean_repo}/releases?per_page=50"
        print(f"Fetching [FULL HISTORY]: {clean_repo}...")
    
    req = urllib.request.Request(api_url)
    
    # Auth
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        req.add_header('Authorization', f'Bearer {token}')
    
    # Add User-Agent header to avoid 403 errors
    req.add_header('User-Agent', 'Mozilla/5.0')
    
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

def analyze_repository_needs(apps):
    """Analyze which repositories need full history scanning"""
    repo_analysis = {}
    
    for app in apps:
        repo = app.get('githubRepo')
        if not repo:
            continue
            
        app_id = app.get('id')
        keyword = app.get('releaseKeyword', '')
        
        if repo not in repo_analysis:
            repo_analysis[repo] = {
                'strategy': 'latest',  # default
                'apps': [],
                'has_keywords': False
            }
        
        repo_analysis[repo]['apps'].append({
            'id': app_id,
            'keyword': keyword
        })
        
        # If any app in this repo has a releaseKeyword, we need full history
        if keyword and len(keyword.strip()) > 0:
            repo_analysis[repo]['has_keywords'] = True
            repo_analysis[repo]['strategy'] = 'list'
    
    return repo_analysis

def main():
    apps = get_apps()
    
    # 1. Analyze repository needs
    repo_analysis = analyze_repository_needs(apps)
    
    # 2. Determine final strategy for each repo
    repo_strategies = {}
    for repo, analysis in repo_analysis.items():
        # Use list strategy if any app has keywords OR if multiple apps use the same repo
        if analysis['has_keywords'] or len(analysis['apps']) > 1:
            repo_strategies[repo] = 'list'
            reason = "multiple apps" if len(analysis['apps']) > 1 else "has releaseKeywords"
            print(f"📦 {repo}: FULL HISTORY scan needed ({reason})")
            print(f"   Apps in this repo: {[app['id'] for app in analysis['apps']]}")
        else:
            repo_strategies[repo] = 'latest'
            print(f"📦 {repo}: LATEST release only")

    # 3. Fetch Data
    mirror_data = {}
    for repo, strategy in repo_strategies.items():
        key, data = fetch_github_data(repo, strategy)
        if data:
            mirror_data[key] = data

    # 4. Save Mirror
    with open(MIRROR_JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(mirror_data, f, indent=2)
    
    print(f"\n✅ Successfully mirrored {len(mirror_data)} repositories to {MIRROR_JSON_FILE}")
    
    # 5. Debug: Show what was found for each app
    print("\n=== APP DISCOVERY RESULTS ===")
    
    for app in apps:
        repo = app.get('githubRepo')
        app_id = app.get('id')
        keyword = app.get('releaseKeyword', '')
        
        if not repo or repo not in mirror_data:
            continue
            
        releases = mirror_data[repo]
        app_found = False
        
        for release in releases:
            for asset in release.get('assets', []):
                asset_name = asset.get('name', '')
                
                # Check if this asset matches the app
                matches = False
                if keyword:
                    # If keyword is specified, check for match
                    matches = keyword.lower() in asset_name.lower()
                else:
                    # If no keyword, this should be the only app in the repo
                    # or we need a different matching strategy
                    matches = True  # This might need refinement
                
                if matches:
                    app_found = True
                    status = "✅" if app_found else "❌"
                    print(f"{status} {app_id}: Found '{asset_name}' in {repo}")
                    break
            
            if app_found:
                break
        
        if not app_found:
            print(f"❌ {app_id}: Not found in {repo}")

if __name__ == "__main__":
    main()
