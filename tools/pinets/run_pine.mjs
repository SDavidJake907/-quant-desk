// Run one Pine Script file on our own candles with PineTS; print its plots as JSON.
//
//   node tools/pinets/run_pine.mjs CANDLES.json SCRIPT.pine ["extra pine line" ...]
//
// CANDLES.json: [{open, high, low, close, volume, openTime(ms)}, ...] oldest first.
// Extra lines are appended to the script (e.g. 'plot(dir, "DIR")') so a value the
// script keeps internal can be read out. Prints {"plot title": [values...], ...}.
// PineTS is AGPL-3.0: used here privately on this machine only.
import { readFileSync } from 'node:fs';
import { PineTS } from 'pinets';

const [candlesPath, scriptPath, ...extra] = process.argv.slice(2);
const candles = JSON.parse(readFileSync(candlesPath, 'utf8'));
const source = readFileSync(scriptPath, 'utf8') + '\n' + extra.join('\n') + '\n';
const pine = new PineTS(candles);
const { plots } = await pine.run(source);
const out = {};
for (const [name, p] of Object.entries(plots)) {
    out[name] = (p.data || []).map((d) => (d && typeof d === 'object' ? d.value : d));
}
process.stdout.write(JSON.stringify(out));
