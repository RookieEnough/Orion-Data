/**
 * APK Hunter - APKDone Download Script
 * 
 * APKDone Anti-Bot Behavior (Nov 2025):
 * - The /download/ page contains a gateway URL (file.apkdone.io/s/.../download)
 * - Direct HTTP requests to gateway URL return 302 → HTML 404 (fake error)
 * - Real APK is only served after browser click with proper cookies/referrer
 * - This script simulates the real browser interaction to bypass protection
 * 
 * Important: APKDone first serves their downloader app, then the real APK.
 * We need to ignore the first download and wait for the actual APK.
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

    // Listen for APK download responses - we need to distinguish between APKDone app and real APK
    const detectedDownloads = [];
    page.on('response', async (response) => {
      const responseUrl = response.url();
      const headers = response.headers();
      
      // Check if this is an APK file
      if (responseUrl.endsWith('.apk') || 
          headers['content-type'] === 'application/vnd.android.package-archive') {
        
        const contentLength = headers['content-length'] || 'unknown';
        console.log(`Detected APK download: ${responseUrl}`);
        console.log(`  Content-Type: ${headers['content-type']}`);
        console.log(`  Content-Length: ${contentLength} bytes`);
        
        detectedDownloads.push({
          url: responseUrl,
          contentLength: parseInt(contentLength) || 0,
          isApkdoneApp: responseUrl.includes('apkdone') && responseUrl.includes('APKDone_')
        });
      }
    });

    console.log(`Navigating to APKDone page: ${url}`);
    await page.goto(url, {
      waitUntil: 'networkidle2',
      timeout: 30000
    });

    // Find and extract the gateway download link
    console.log('Searching for download button...');
    const gatewayUrl = await page.evaluate(() => {
      // Look for any link that contains file.apkdone.io and /download
      const links = Array.from(document.querySelectorAll('a[href*="file.apkdone.io"]'));
      const downloadLink = links.find(link => link.href.includes('/download'));
      return downloadLink ? downloadLink.href : null;
    });

    if (!gatewayUrl) {
      throw new Error('Could not find APKDone gateway download link on page');
    }

    console.log(`Found gateway URL: ${gatewayUrl}`);

    // Clear any previously detected downloads before clicking
    detectedDownloads.length = 0;

    // Click the download button to trigger the real download flow
    console.log('Clicking download button to activate session...');
    await page.click('a[href*="file.apkdone.io"]');
    
    // Wait for downloads to be detected - APKDone typically serves their app first, then the real APK
    console.log('Waiting for APK downloads to be detected...');
    await page.waitForTimeout(8000);
    
    // Analyze detected downloads
    console.log(`Detected ${detectedDownloads.length} APK downloads during session`);
    
    let realApkUrl = null;
    
    if (detectedDownloads.length === 0) {
      throw new Error('No APK downloads detected after button click');
    } else if (detectedDownloads.length === 1) {
      // If only one download detected, use it (might be the real APK or APKDone app)
      realApkUrl = detectedDownloads[0].url;
      console.log(`Single download detected, using: ${realApkUrl}`);
    } else {
      // Multiple downloads - filter out APKDone app and choose the largest file (usually the real APK)
      const nonApkdoneDownloads = detectedDownloads.filter(d => !d.isApkdoneApp);
      
      if (nonApkdoneDownloads.length > 0) {
        // Choose the largest file from non-APKDone downloads
        nonApkdoneDownloads.sort((a, b) => b.contentLength - a.contentLength);
        realApkUrl = nonApkdoneDownloads[0].url;
        console.log(`Selected largest non-APKDone download: ${realApkUrl}`);
      } else {
        // All downloads are from APKDone, choose the largest one
        detectedDownloads.sort((a, b) => b.contentLength - a.contentLength);
        realApkUrl = detectedDownloads[0].url;
        console.log(`Selected largest download (may be APKDone app): ${realApkUrl}`);
      }
    }

    if (!realApkUrl) {
      throw new Error('Could not determine real APK download URL');
    }

    console.log(`Final APK download URL: ${realApkUrl}`);
    
    // Close browser before starting the actual download
    await browser.close();
    console.log('Browser closed, starting APK download...');

    // Download the APK using the final URL
    await downloadDirect(realApkUrl, outputPath);
    
    // Verify the downloaded file is reasonable size (not the tiny APKDone app)
    const stats = fs.statSync(outputPath);
    console.log(`Downloaded file size: ${stats.size} bytes`);
    
    if (stats.size < 10000000) { // Less than 10MB is suspicious for a real app
      console.warn('Warning: Downloaded file is very small (' + stats.size + ' bytes). This might be the APKDone downloader app instead of the actual APK.');
      // Don't fail here, as some apps might actually be small
    }
    
  } catch (error) {
    await browser.close();
    throw error;
  }
}
