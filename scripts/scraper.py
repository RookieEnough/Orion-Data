from utils import setup_session, extract_version_info
from bs4 import BeautifulSoup
import re
import time
import urllib.parse

class GetModsApkScraper:
    def __init__(self):
        self.session = setup_session()
        self.base_domain = "https://getmodsapk.com"
    
    def get_download_links(self, base_url):
        """Get download links following the multi-step process"""
        try:
            print(f"🔍 Starting download process for: {base_url}")
            
            # Step 1: Navigate to base URL
            print(f"📄 Step 1: Accessing main page...")
            response = self.session.get(base_url)
            response.raise_for_status()
            
            # Step 2: Go to download page
            download_page_url = base_url.rstrip('/') + '/download/'
            print(f"📥 Step 2: Accessing download page...")
            response = self.session.get(download_page_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Debug: Save HTML for inspection
            with open('debug_page.html', 'w', encoding='utf-8') as f:
                f.write(soup.prettify())
            
            # Step 3: Find all potential download links
            print(f"🔗 Step 3: Finding download links...")
            
            # Method 1: Look for links containing '/download/'
            download_links = soup.find_all('a', href=re.compile(r'/download/\d+/', re.I))
            
            if not download_links:
                # Method 2: Look for buttons with download text
                download_buttons = soup.find_all(['a', 'button'], 
                                               string=re.compile(r'download|begin download', re.I))
                for button in download_buttons:
                    href = button.get('href', '')
                    if href and '/download/' in href:
                        download_links.append(button)
            
            if not download_links:
                # Method 3: Look for any links with download in class or id
                download_links = soup.find_all(['a', 'div'], 
                                             attrs={'class': re.compile(r'download', re.I),
                                                   'href': re.compile(r'.*')})
            
            print(f"📎 Found {len(download_links)} potential download links")
            
            # Step 4: Try each download link
            for i, link in enumerate(download_links[:5]):  # Limit to first 5 to avoid too many requests
                href = link.get('href', '')
                if not href:
                    continue
                    
                # Construct full URL
                if href.startswith('/'):
                    download_id_url = self.base_domain + href
                elif href.startswith('http'):
                    download_id_url = href
                else:
                    download_id_url = urllib.parse.urljoin(self.base_domain, href)
                
                print(f"🔍 Trying download link {i+1}: {download_id_url}")
                
                try:
                    # Step 4: Get final download page
                    final_response = self.session.get(download_id_url)
                    final_response.raise_for_status()
                    
                    final_soup = BeautifulSoup(final_response.content, 'html.parser')
                    
                    # Extract direct APK download link
                    apk_link = self.extract_direct_apk_link(final_soup, download_id_url)
                    if apk_link:
                        print(f"✅ Success! Found APK: {apk_link}")
                        return apk_link
                    
                    time.sleep(1)  # Be nice to the server
                    
                except Exception as e:
                    print(f"❌ Failed with link {i+1}: {e}")
                    continue
            
            # If all methods fail, try JavaScript-based extraction
            return self.extract_from_javascript(soup, base_url)
            
        except Exception as e:
            print(f"❌ Error in download process: {e}")
            return None
    
    def extract_direct_apk_link(self, soup, page_url):
        """Extract direct APK download link from final page"""
        print(f"🔍 Extracting APK link from: {page_url}")
        
        # Method 1: Direct .apk links
        apk_links = soup.find_all('a', href=re.compile(r'\.apk($|\?|#)', re.I))
        for link in apk_links:
            href = link.get('href', '')
            if href:
                full_url = href if href.startswith('http') else urllib.parse.urljoin(self.base_domain, href)
                print(f"📦 Found direct APK link: {full_url}")
                return full_url
        
        # Method 2: Look for download buttons with data attributes
        download_elements = soup.find_all(attrs={
            'data-download': True,
            'href': re.compile(r'.*')
        })
        for element in download_elements:
            href = element.get('href', '')
            if href and '.apk' in href.lower():
                full_url = href if href.startswith('http') else urllib.parse.urljoin(self.base_domain, href)
                print(f"📦 Found data-download APK: {full_url}")
                return full_url
        
        # Method 3: Look for iframes or redirects
        iframes = soup.find_all('iframe', src=re.compile(r'.*'))
        for iframe in iframes:
            src = iframe.get('src', '')
            if src and '.apk' in src.lower():
                full_url = src if src.startswith('http') else urllib.parse.urljoin(self.base_domain, src)
                print(f"📦 Found iframe APK: {full_url}")
                return full_url
        
        # Method 4: Extract from JavaScript variables
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string:
                # Look for various URL patterns in JavaScript
                patterns = [
                    r'https?://[^"\']*\.apk[^"\']*',
                    r'downloadUrl\s*[=:]\s*["\']([^"\']*\.apk[^"\']*)["\']',
                    r'fileUrl\s*[=:]\s*["\']([^"\']*\.apk[^"\']*)["\']',
                    r'href\s*[=:]\s*["\']([^"\']*\.apk[^"\']*)["\']'
                ]
                
                for pattern in patterns:
                    matches = re.findall(pattern, script.string, re.I)
                    for match in matches:
                        if isinstance(match, tuple):
                            match = match[0]  # Get the first group if it's a tuple
                        full_url = match if match.startswith('http') else urllib.parse.urljoin(self.base_domain, match)
                        print(f"📦 Found JavaScript APK: {full_url}")
                        return full_url
        
        print(f"❌ No APK link found on {page_url}")
        return None
    
    def extract_from_javascript(self, soup, base_url):
        """Alternative extraction method for JavaScript-heavy pages"""
        print("🔄 Trying JavaScript-based extraction...")
        
        # Look for scripts that might contain download logic
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and 'download' in script.string.lower():
                # Extract any URLs that might be download endpoints
                url_patterns = [
                    r'https?://[^"\']*/download/[^"\']*',
                    r'https?://[^"\']*/file/[^"\']*',
                    r'https?://[^"\']*\.apk[^"\']*'
                ]
                
                for pattern in url_patterns:
                    matches = re.findall(pattern, script.string, re.I)
                    for match in matches:
                        if 'getmodsapk' in match.lower():
                            print(f"🔗 Found potential JS download: {match}")
                            # Try to access this URL
                            try:
                                response = self.session.get(match)
                                if response.status_code == 200:
                                    js_soup = BeautifulSoup(response.content, 'html.parser')
                                    apk_link = self.extract_direct_apk_link(js_soup, match)
                                    if apk_link:
                                        return apk_link
                            except:
                                continue
        
        return None
    
    def get_current_version(self, base_url):
        """Get current version from the website"""
        try:
            response = self.session.get(base_url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Look for version in multiple places
            version_pattern = r'v?(\d+\.\d+\.\d+)'
            
            # Check page title and headings
            title = soup.find('title')
            if title:
                version_match = re.search(version_pattern, title.get_text(), re.I)
                if version_match:
                    return version_match.group(0)
            
            # Check main content
            main_content = soup.find('main') or soup.find('article') or soup.find('div', class_=re.compile(r'content|main', re.I))
            if main_content:
                version_match = re.search(version_pattern, main_content.get_text(), re.I)
                if version_match:
                    return version_match.group(0)
            
            # Check specific version elements
            version_elements = soup.find_all(['span', 'div', 'p'], 
                                           string=re.compile(r'v?\d+\.\d+\.\d+', re.I))
            for element in version_elements:
                version = extract_version_info(element.get_text())
                if version:
                    return version
            
            # Fallback: extract from any text
            page_text = soup.get_text()
            version_match = re.search(version_pattern, page_text, re.I)
            if version_match:
                return version_match.group(0)
            
            return None
            
        except Exception as e:
            print(f"❌ Error getting current version: {e}")
            return None
