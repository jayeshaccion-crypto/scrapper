const fs = require('fs');
const path = require('path');

const outputDir = path.join(__dirname, '..', 'output');
const dataFile = path.join(__dirname, 'data.json');
const sites = ['99acres', 'magicbricks', 'squareyards', 'olx', 'proptiger'];

// Load existing data
let existing = [];
if (fs.existsSync(dataFile)) {
  try { existing = JSON.parse(fs.readFileSync(dataFile, 'utf8')); } catch {}
}

// Build key set for dedup (site + url, site + prop_id)
const seen = new Set();
for (const p of existing) {
  const k = p.site_name + '|' + (p.prop_id || p.listing_url || p.url || '');
  if (k) seen.add(k);
}

const newProps = [];
for (const site of sites) {
  const siteDir = path.join(outputDir, site);
  if (!fs.existsSync(siteDir)) continue;
  const files = fs.readdirSync(siteDir).filter(f => f.endsWith('.json') && f !== '.seen.json');
  for (const file of files) {
    try {
      let content = fs.readFileSync(path.join(siteDir, file), 'utf8');
      if (content.charCodeAt(0) === 0xFEFF) content = content.slice(1);
      const data = JSON.parse(content);
      const arr = Array.isArray(data) ? data : [data];
      for (const item of arr) {
        const k = item.site_name + '|' + (item.prop_id || item.listing_url || item.url || '');
        if (k && seen.has(k)) continue;
        if (k) seen.add(k);
        newProps.push(item);
      }
    } catch (e) {
      console.error(`Error reading ${file}:`, e.message);
    }
  }
}

const merged = [...existing, ...newProps];
fs.writeFileSync(dataFile, JSON.stringify(merged, null, 2));
console.log(`Existing: ${existing.length}, New: ${newProps.length}, Total: ${merged.length} properties`);
