#!/usr/bin/env node
/**
 * KICD CBC Curriculum Designs — Termux Puppeteer Screenshot & Text Extractor
 * =========================================================================
 * Designed specifically for Android Termux & Linux/macOS environments.
 *
 * Termux Quick Setup:
 *   pkg update && pkg upgrade -y
 *   pkg install nodejs chromium -y
 *   npm install puppeteer-core
 *
 * Usage:
 *   node termux_kicd_capture.js
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// ──────────────────────────────────────────────
// CONFIG
// ──────────────────────────────────────────────
const OUTPUT_DIR = path.join(__dirname, 'kicd_media_captures');
const MANIFEST_FILE = path.join(OUTPUT_DIR, 'capture_manifest.json');

const GRADE_PAGES = [
    { grade: "Pre-Primary_1",                  url: "https://kicd.ac.ke/cbc-materials/pre-primary/" },
    { grade: "Pre-Primary_2",                  url: "https://kicd.ac.ke/cbc-materials/pre-primary/" },
    { grade: "Grade_1",                        url: "https://kicd.ac.ke/cbc-materials/lower-primary/" },
    { grade: "Grade_2",                        url: "https://kicd.ac.ke/cbc-materials/lower-primary/" },
    { grade: "Grade_3",                        url: "https://kicd.ac.ke/cbc-materials/lower-primary/" },
    { grade: "Grade_4",                        url: "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-four-designs/" },
    { grade: "Grade_5",                        url: "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-five-designs/" },
    { grade: "Grade_6",                        url: "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-six-designs/" },
    { grade: "Grade_7",                        url: "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-seven-designs/" },
    { grade: "Grade_8",                        url: "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-eight-designs/" },
    { grade: "Grade_9",                        url: "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-nine-designs/" },
    { grade: "Grade_10",                       url: "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-ten/" },
    { grade: "Grade_11",                       url: "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-eleven/" },
    { grade: "Grade_12",                       url: "https://kicd.ac.ke/cbc-materials/curriculum-designs/grade-twelve/" },
    { grade: "Diploma_in_Teacher_Education",   url: "https://kicd.ac.ke/cbc-materials/curriculum-designs/diploma-in-teacher-education/" }
];

function findChromium() {
    const candidates = [
        '/data/data/com.termux/files/usr/bin/chromium',
        '/data/data/com.termux/files/usr/bin/chromium-browser',
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/usr/bin/google-chrome',
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    ];

    for (const bin of ['chromium', 'chromium-browser', 'google-chrome']) {
        try {
            const found = execSync(`which ${bin}`, { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim();
            if (found && fs.existsSync(found)) return found;
        } catch (e) {}
    }

    for (const p of candidates) {
        if (fs.existsSync(p)) return p;
    }
    return null;
}

function safeName(str) {
    return str.replace(/[^\w\s\-]/g, '').trim().replace(/\s+/g, '_');
}

async function run() {
    let puppeteer;
    try {
        puppeteer = require('puppeteer-core');
    } catch (e) {
        try {
            puppeteer = require('puppeteer');
        } catch (err) {
            console.error("❌ Puppeteer is not installed.");
            console.error("   Install it in Termux using:  npm install puppeteer-core");
            process.exit(1);
        }
    }

    const chromiumPath = findChromium();
    console.log("=" .repeat(65));
    console.log("  KICD CBC Curriculum Designs — Termux Node.js Capture");
    console.log("=" .repeat(65));

    if (chromiumPath) {
        console.log(`  🔍 Detected System Chromium: ${chromiumPath}`);
    } else {
        console.log("  ⚠️  System Chromium binary not found. Set executablePath or install via `pkg install chromium`");
    }

    if (!fs.existsSync(OUTPUT_DIR)) {
        fs.mkdirSync(OUTPUT_DIR, { recursive: true });
    }

    const launchArgs = {
        headless: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
            '--single-process',
            '--no-zygote'
        ]
    };

    if (chromiumPath) {
        launchArgs.executablePath = chromiumPath;
    }

    const browser = await puppeteer.launch(launchArgs);
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 960 });

    const records = [];
    const visitedUrls = new Set();

    for (const item of GRADE_PAGES) {
        const grade = item.grade;
        const url = item.url;

        console.log(`\n📱 Processing ${grade}...`);
        const gradeDir = path.join(OUTPUT_DIR, safeName(grade));
        if (!fs.existsSync(gradeDir)) {
            fs.mkdirSync(gradeDir, { recursive: true });
        }

        if (!visitedUrls.has(url)) {
            console.log(`   Navigating to ${url}`);
            try {
                await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
                await new Promise(r => setTimeout(r, 2000));

                // Scroll down to load all lazy-loaded components & iframes
                console.log("   Scrolling to load all Google Drive viewers...");
                for (let i = 0; i < 5; i++) {
                    await page.evaluate(() => window.scrollBy(0, 1000));
                    await new Promise(r => setTimeout(r, 1000));
                }
                await page.evaluate(() => window.scrollTo(0, 0));
                await new Promise(r => setTimeout(r, 1000));

                visitedUrls.add(url);
            } catch (e) {
                console.warn(`   ⚠️ Navigation warning: ${e.message}`);
            }
        }

        // Full Page Screenshot
        const fullSsPath = path.join(gradeDir, `${safeName(grade)}_full_page.png`);
        await page.screenshot({ path: fullSsPath, fullPage: true });
        console.log(`   📸 Saved Full Page Screenshot: ${path.basename(fullSsPath)}`);

        # Extract inner text of body
        const bodyText = await page.evaluate(() => document.body.innerText);
        const textPath = path.join(gradeDir, `${safeName(grade)}_content.txt`);
        fs.writeFileSync(textPath, bodyText, 'utf-8');
        console.log(`   📄 Saved Extracted Text: ${path.basename(textPath)}`);

        // Find all Google Drive iframes on the page
        const iframeElements = await page.$$("iframe[src*='drive.google.com']");
        console.log(`   Found ${iframeElements.length} embedded viewer(s)`);

        for (let idx = 0; idx < iframeElements.length; idx++) {
            const iframe = iframeElements[idx];
            try {
                const src = await page.evaluate(el => el.src, iframe);
                const fileIdMatch = src.match(/\/file\/d\/([A-Za-z0-9_\-]+)/);
                const fileId = fileIdMatch ? fileIdMatch[1] : `doc_${idx + 1}`;

                // Extract nearest preceding heading name
                const subjectName = await page.evaluate(el => {
                    let prev = el.previousElementSibling;
                    while (prev) {
                        if (['H2', 'H3'].includes(prev.tagName)) return prev.innerText.trim();
                        prev = prev.previousElementSibling;
                    }
                    return null;
                }, iframe) || `Document_${idx + 1}`;

                const subjectSafe = safeName(subjectName);
                const iframeSsPath = path.join(gradeDir, `${subjectSafe}_preview.png`);

                // Scroll iframe into view and screenshot element
                await iframe.evaluate(el => el.scrollIntoView());
                await new Promise(r => setTimeout(r, 500));
                await iframe.screenshot({ path: iframeSsPath });

                console.log(`   📸 [${idx + 1}/${iframeElements.length}] ${subjectName} -> ${path.basename(iframeSsPath)}`);

                records.push({
                    grade: grade,
                    subject: subjectName,
                    file_id: fileId,
                    screenshot_path: path.relative(OUTPUT_DIR, iframeSsPath),
                    text_path: path.relative(OUTPUT_DIR, textPath),
                    captured_at: new Date().toISOString()
                });
            } catch (err) {
                console.warn(`   ⚠️ Frame ${idx + 1} capture skipped: ${err.message}`);
            }
        }
    }

    browser.close();

    fs.writeFileSync(MANIFEST_FILE, JSON.stringify(records, null, 2), 'utf-8');
    console.log("\n" + "=".repeat(65));
    console.log("🎉 Termux Capture Complete!");
    console.log(`   Manifest: ${MANIFEST_FILE}`);
    console.log(`   Total Captured: ${records.length} items`);
    console.log("=".repeat(65));
}

run().catch(console.error);
