/**
 * APK Hunter - APKDone Download Script
 * 
 * APKDone Anti-Bot Behavior (Nov 2025):
 * - The /download/ page contains a gateway URL (file.apkdone.io/s/.../download)
 * - Direct HTTP requests to gateway URL return 302 → HTML 404 (fake error)
 * - Real APK is only served after browser click with proper cookies/referrer
 * - This script simulates the real browser interaction to bypass protection
 * 
 * Important: There are two download buttons:
 * 1. Transparent "Download APK (size)" button - the real APK we want
 * 2. APKDone downloader app button - their own app (small file)
 */

const puppeteer = require('puppeteer');
const https = require('https');
const fs = require('fs');
const path = require('path');

(async () => {
  try {
    // Parse command line arguments
    const args = parseArgs();
    const configId = args.id;
    const outputFile = args.out;
    
    if (!configId || !outputFile) {
      throw new Error('Missing required arguments: --id and --out are required');
    }

    console.log(`Starting APK Hunter for ID: ${configId}, output: ${outputFile}`);

    // Read mirror_config.json from repository root
    const configPath = path.resolve(process.cwd(), 'mirror_config.json');
    console.log(`Reading config from: ${configPath}`);
    
    if (!fs.existsSync(configPath)) {
      throw new Error('mirror_config.json not found in repository root');
    }

    const configData = fs.readFileSync(configPath, 'utf8');
    const config = JSON.parse(configData);
    
    const appConfig = config.find(item => item.id === configId);
    if (!appConfig) {
      throw new Error(`Config entry with id '${configId}' not found in mirror_config.json`);
    }

    console.log(`Found app: ${appConfig.name}, mode: ${appConfig.mode}, URL: ${appConfig.downloadUrl}`);

    const outputPath = path.resolve(process.cwd(), outputFile);
    
    if (appConfig.mode === 'direct') {
      await downloadDirect(appConfig.downloadUrl, outputPath);
    } else if (appConfig.mode === 'scrape') {
      await downloadWithScrape(appConfig.downloadUrl, outputPath, appConfig.name);
    } else {
      throw new Error(`Unknown mode: ${appConfig.mode}`);
    }

    console.log(`Successfully downloaded APK to: ${outputPath}`);
    process.exit(0);
    
  } catch (error) {
    console.error('APK Hunter failed: ' + error.message);
    process.exit(1);
  }
})();

function parseArgs() {
  const args = {};
  for (let i = 2; i < process.argv.length; i++) {
    if (process.argv[i] === '--id' && process.argv[i + 1]) {
      args.id = process.argv[++i];
    } else if (process.argv[i] === '--out' && process.argv[i + 1]) {
      args.out = process.argv[++i];
    }
  }
  return args;
}

async function downloadDirect(url, outputPath) {
  return new Promise((resolve, reject) => {
    console.log(`Starting direct download from: ${url}`);
    
    const file = fs.createWriteStream(outputPath);
    let attempts = 0;
    const maxAttempts = 2;

    function attemptDownload() {
      attempts++;
      console.log(`Direct download attempt ${attempts}/${maxAttempts}`);
      
      const request = https.get(url, (response) => {
        if (response.statusCode === 200) {
          const contentLength = response.headers['content-length'];
          console.log(`Downloading APK (${contentLength} bytes)`);
          
          response.pipe(file);
          
          file.on('finish', () => {
            file.close();
            console.log('Direct download completed successfully');
            resolve();
          });
          
          response.on('error', (error) => {
            file.close();
            if (fs.existsSync(outputPath)) {
              fs.unlinkSync(outputPath);
            }
            reject(new Error('Network error during download: ' + error.message));
          });
          
        } else if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
          // Follow redirects for direct mode
          console.log(`Following redirect to: ${response.headers.location}`);
          attemptDownload(response.headers.location);
        } else {
          reject(new Error(`HTTP ${response.statusCode}: ${response.statusMessage}`));
        }
      });
      
      request.setTimeout(120000, () => {
        request.destroy();
        reject(new Error('Direct download timeout after 120 seconds'));
      });
      
      request.on('error', (error) => {
        if (attempts < maxAttempts) {
          console.log(`Retrying after error: ${error.message}`);
          setTimeout(attemptDownload, 2000);
        } else {
          reject(new Error('Direct download failed after ' + maxAttempts + ' attempts: ' + error.message));
        }
      });
    }
    
    attemptDownload();
  });
}

async function downloadWithScrape(url, outputPath, appName) {
  console.log('Starting APKDone scrape mode download');
  
  const browser = await puppeteer.launch({
    headless: 'new',
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-accelerated-2d-canvas',
      '--disable-gpu',
      '--window-size=1920,1080'
    ],
    timeout: 120000
  });

  try {
    const page = await browser.newPage();
    
    // Block unnecessary resources for speed
    await page.setRequestInterception(true);
    page.on('request', (req) => {
      const resourceType = req.resourceType();
      if (['image', 'font', 'stylesheet'].includes(resourceType)) {
        req.abort();
      } else {
        req.continue();
      }
    });

    // Listen for APK download responses
    let realApkUrl = null;
    let apkContentLength = 0;
    
    page.on('response', async (response) => {
      const responseUrl = response.url();
      const headers = response.headers();
      
      // Check if this is an APK file and NOT the APKDone downloader
      if ((responseUrl.endsWith('.apk') || 
          headers['content-type'] === 'application/vnd.android.package-archive') &&
          !responseUrl.includes('APKDone_') && // Filter out APKDone app
          !responseUrl.includes('apkdone_mod_apkdone')) {
        
        const contentLength = parseInt(headers['content-length']) || 0;
        console.log(`Detected potential real APK download: ${responseUrl}`);
        console.log(`  Content-Type: ${headers['content-type']}`);
        console.log(`  Content-Length: ${contentLength} bytes`);
        
        // Prefer larger files (real APKs are usually > 100MB)
        if (contentLength > apkContentLength) {
          realApkUrl = responseUrl;
          apkContentLength = contentLength;
          console.log(`  Selected as candidate (larger file)`);
        }
      }
    });

    console.log(`Navigating to APKDone page: ${url}`);
    await page.goto(url, {
      waitUntil: 'networkidle2',
      timeout: 30000
    });

    // Find the correct download button - look for the transparent "Download APK" button
    console.log('Searching for the real APK download button...');
    
    // Strategy 1: Look for button with text containing "Download APK" and size info
    const realDownloadButton = await page.evaluate(() => {
      // Look for buttons or links that contain "Download APK" and have size information
      const allElements = Array.from(document.querySelectorAll('a, button, div[onclick]'));
      
      for (const element of allElements) {
        const text = element.textContent || '';
        if (text.includes('Download APK') && (text.includes('MB') || text.includes('GB'))) {
          return {
            element: element.outerHTML.substring(0, 200), // For logging
            href: element.href,
            text: text.trim()
          };
        }
      }
      
      // Strategy 2: Look for the gateway URL that's NOT the APKDone app
      const gatewayLinks = Array.from(document.querySelectorAll('a[href*="file.apkdone.io"]'));
      for (const link of gatewayLinks) {
        const parentText = link.closest('div')?.textContent || '';
        if (parentText.includes('Download APK') && (parentText.includes('MB') || parentText.includes('GB'))) {
          return {
            element: link.outerHTML.substring(0, 200),
            href: link.href,
            text: parentText.trim()
          };
        }
      }
      
      return null;
    });

    if (!realDownloadButton) {
      throw new Error('Could not find the real APK download button on page');
    }

    console.log(`Found real download button: ${realDownloadButton.text}`);
    console.log(`Gateway URL: ${realDownloadButton.href}`);

    // Click the real download button
    console.log('Clicking the real APK download button...');
    
    // Use multiple strategies to click the button
    try {
      // Strategy 1: Click using text selector
      await page.click('a:has-text("Download APK")');
    } catch (error) {
      try {
        // Strategy 2: Click using the gateway URL
        await page.click(`a[href="${realDownloadButton.href}"]`);
      } catch (error2) {
        // Strategy 3: Use JavaScript to trigger click
        await page.evaluate((href) => {
          const link = document.querySelector(`a[href="${href}"]`);
          if (link) {
            link.click();
          }
        }, realDownloadButton.href);
      }
    }

    // Wait for the real APK download to be detected (longer wait for large files)
    console.log('Waiting for real APK download to be detected...');
    await page.waitForTimeout(10000);
    
    if (!realApkUrl) {
      console.log('Real APK URL not detected yet, waiting longer...');
      await page.waitForTimeout(5000);
    }

    if (!realApkUrl) {
      // Last resort: try to get current URL in case we navigated directly to APK
      const currentUrl = page.url();
      if (currentUrl.endsWith('.apk') && !currentUrl.includes('APKDone_')) {
        realApkUrl = currentUrl;
        console.log(`Using current URL as APK: ${realApkUrl}`);
      } else {
        throw new Error('Could not detect real APK download URL after clicking button');
      }
    }

    console.log(`Final real APK download URL: ${realApkUrl}`);
    console.log(`Expected file size: ${apkContentLength} bytes`);
    
    // Close browser before starting the actual download
    await browser.close();
    console.log('Browser closed, starting real APK download...');

    // Download the real APK
    await downloadDirect(realApkUrl, outputPath);
    
    // Verify the downloaded file size matches expectations
    const stats = fs.statSync(outputPath);
    console.log(`Downloaded file size: ${stats.size} bytes`);
    
    if (stats.size < 100000000) { // Less than 100MB is suspicious for CapCut
      console.warn('Warning: Downloaded file is smaller than expected for CapCut (' + stats.size + ' bytes).');
    }
    
    if (apkContentLength > 0 && Math.abs(stats.size - apkContentLength) > 1000000) {
      console.warn('Warning: Downloaded file size differs significantly from expected size.');
    }
    
  } catch (error) {
    await browser.close();
    throw error;
  }
}
