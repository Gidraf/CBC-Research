#!/usr/bin/env node
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');
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
    { grade: "Diploma_DTE",                    url: "https://kicd.ac.ke/cbc-materials/curriculum-designs/diploma-in-teacher-education/" }
];
function findChromium() {
    const candidates = [
        '/data/data/com.termux/files/usr/bin/chromium',
        '/data/data/com.termux/files/usr/bin/chromium-browser',
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser'
    ];
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
        puppeteer = require('puppeteer');
    }
    const chromiumPath = findChromium();
    console.log("=" .repeat(60));
    console.log("  KICD CBC Termux Node.js Capture");
    console.log("=" .repeat(60));
    if (!fs.existsSync(OUTPUT_DIR)) fs.mkdirSync(OUTPUT_DIR, { recursive: true });
    const launchArgs = {
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage', '--single-process']
    };
    if (chromiumPath) launchArgs.executablePath = chromiumPath;
    const browser = await puppeteer.launch(launchArgs);
    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 960 });
    for (const item of GRADE_PAGES) {
        console.log(`Processing ${item.grade}...`);
        const gdir = path.join(OUTPUT_DIR, safeName(item.grade));
        if (!fs.existsSync(gdir)) fs.mkdirSync(gdir, { recursive: true });
        await page.goto(item.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
        for (let i = 0; i < 4; i++) {
            await page.evaluate(() => window.scrollBy(0, 1000));
            await new Promise(r => setTimeout(r, 1000));
        }
        // Full page screenshot & text extraction
        const fullSsPath = path.join(gdir, `${safeName(item.grade)}_full.png`);
        await page.screenshot({ path: fullSsPath, fullPage: true });
        // Extract inner text of body
        const bodyText = await page.evaluate(() => document.body.innerText);
        fs.writeFileSync(path.join(gdir, `${safeName(item.grade)}_text.txt`), bodyText, 'utf-8');
        console.log(`   Saved ${item.grade} text and full screenshot`);
    }
    await browser.close();
    console.log("\nCapture finished!");
}

run().catch(console.error);