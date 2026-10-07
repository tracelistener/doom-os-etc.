// Test the actual shipped JS applier and page event handlers with a minimal DOM.
// This is not a visual browser test or a device/DOOM OS runtime test.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const root = path.join(__dirname, '..');
const input = process.argv[2];
if (!input) throw new Error('Usage: node synth/test_browser.cjs /path/to/stock/SP404MKII_APP1.bin');
const stock = new Uint8Array(fs.readFileSync(input));
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

function load(includeSynth = true, includeWaves = false) {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, {
      id, checked: id === 'synthPoly' && /id="synthPoly" checked/.test(elements.get('doomos-patcher')?.innerHTML || ''),
      hidden: false, disabled: false, value: '', listeners: {},
      classList: { add() {}, remove() {} },
      addEventListener(event, fn) { this.listeners[event] = fn; },
      click() {},
    });
    return elements.get(id);
  };
  let start, blob = null;
  const context = vm.createContext({
    window: { addEventListener() {} },
    document: {
      readyState: 'loading',
      addEventListener(event, fn) { if (event === 'DOMContentLoaded') start = fn; },
      querySelectorAll() { return []; },
      getElementById(id) {
        if (['synthPoly','synthWaves'].includes(id) && !get('doomos-patcher').innerHTML.includes('id="' + id + '"')) return null;
        return get(id);
      },
    },
    module: { exports: {} },
    setTimeout(fn) { fn(); },
    Blob,
    URL: { createObjectURL(value) { blob = value; return 'blob:test'; }, revokeObjectURL() { blob = null; } },
  });
  if (includeSynth) vm.runInContext(fs.readFileSync(path.join(root, 'synth-data.js'), 'utf8'), context);
  if (includeWaves) vm.runInContext(fs.readFileSync(path.join(root, 'waves-data.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(root, 'doomos-patcher.js'), 'utf8'), context);
  start();
  return { context, get, api: context.module.exports, blob: () => blob };
}

async function choose(page, bytes, name = 'SP404MKII_APP1.bin') {
  page.get('file').files = [{ name, size: bytes.length, async arrayBuffer() { return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength); } }];
  await page.get('file').listeners.change();
}

(async () => {
  const page = load();
  const { api } = page;
  for (let len=0;len<128;len++) {
    const original = Uint8Array.from({length:len},(_,i)=>(i*73+len)&255);
    assert.equal(Buffer.from(api.fromBase64(Buffer.from(original).toString('base64'))).toString('hex'),Buffer.from(original).toString('hex'));
  }
  assert.equal(api.hex(api.sha256(stock)), digest(stock), 'browser SHA matches Node');
  const doom = api.applyPatch(api.fromBase64(page.context.window.DOOMOS_PATCH), stock);
  assert.equal(doom.digest, page.context.window.DOOMOS_SYNTH.base);
  const overlay = api.fromBase64(page.context.window.DOOMOS_SYNTH_PATCHES.poly);
  const merged = api.applyPatch(overlay, doom.bytes);
  assert.equal(merged.digest, page.context.window.DOOMOS_SYNTH.outputs.poly.sha256);
  assert.equal(merged.bytes.length, page.context.window.DOOMOS_SYNTH.outputs.poly.size);
  const corrupt = overlay.slice(); corrupt[corrupt.length - 1] ^= 1;
  assert.throws(() => api.applyPatch(corrupt, doom.bytes), /came out wrong/);
  assert.throws(() => api.applyPatch(overlay, stock), /unsupported firmware/);

  await choose(page, stock);
  assert.equal(page.get('patch').disabled, false);
  await page.get('patch').listeners.click();
  assert.equal(page.get('synthPoly').disabled, true);
  assert.equal(page.get('download').hidden, false);
  assert.equal(digest(new Uint8Array(await page.blob().arrayBuffer())), merged.digest);
  assert.match(page.get('status').textContent, /Sound Generator \(4 voices\)/);

  page.get('reset').listeners.click();
  assert.equal(page.get('download').hidden, true);
  assert.equal(page.get('synthPoly').disabled, false);
  page.get('synthPoly').checked = false;
  await choose(page, stock);
  await page.get('patch').listeners.click();
  assert.equal(digest(new Uint8Array(await page.blob().arrayBuffer())), doom.digest);

  const original = load(false);
  await choose(original, stock);
  await original.get('patch').listeners.click();
  assert.equal(digest(new Uint8Array(await original.blob().arrayBuffer())), doom.digest);

  page.get('reset').listeners.click();
  await choose(page, merged.bytes);
  assert.equal(page.get('patch').disabled, true);
  assert.equal(page.get('download').hidden, true);
  assert.match(page.get('status').textContent, /not a firmware file this patcher knows/);
  await choose(page, new Uint8Array([1, 2, 3]), 'SP404MKII_APP0.bin');
  assert.match(page.get('status').textContent, /This is SP404MKII_APP0.bin/);

  // A failed overlay must never create a downloadable corrupt result.
  const broken = load();
  broken.context.window.DOOMOS_SYNTH_PATCHES.poly = Buffer.from(corrupt).toString('base64');
  await choose(broken, stock);
  await broken.get('patch').listeners.click();
  assert.equal(broken.blob(), null);
  assert.equal(broken.get('download').hidden, true);
  assert.match(broken.get('status').textContent, /came out wrong/);

  const wavePage = load(true, true);
  assert.equal(wavePage.get('synthPoly').checked,false,'combined candidate remains opt-in');
  const panel = wavePage.get('doomos-patcher').innerHTML;
  assert.equal((panel.match(/type="checkbox"/g) || []).length,1,'one combined option');
  assert.match(panel,/Sound Generator v9\.1 \(4 voices \+ 17 new waves \+ envelopes \+ voice stealing\)/);
  assert.doesNotMatch(panel,/synthWaves|Experimental fork addition|Shared controls|No DUTY smoother/);
  const html = fs.readFileSync(path.join(root,'index.html'),'utf8');
  assert.doesNotMatch(html,/For V8, start at modest LEVEL|Uncheck Wave Lab|Expected Wave Lab V8 APP1/);
  assert.equal(wavePage.context.window.DOOMOS_WAVES.revision,'v9.1-ram');
  assert.equal(wavePage.context.window.DOOMOS_WAVES.sha256,'dad6f0d4baa3d26a613b86dc92e7ab1d3765c10e8a532e2dd3eefdf5093a1c33');
  assert.equal(wavePage.context.window.DOOMOS_WAVES.size,2966032);
  assert.equal(wavePage.context.window.DOOMOS_WAVES.execution,'sdram-gap');
  assert.equal(wavePage.context.window.DOOMOS_WAVES.pool_unchanged_from_v4,true);
  assert.equal(wavePage.context.window.DOOMOS_WAVES.polyblep.length,0);
  assert.match(wavePage.context.window.DOOMOS_WAVES.duty,/raw.*no smoothing/);
  assert.equal(wavePage.context.window.DOOMOS_WAVES.status,'owner-reported-hardware-working');
  assert.equal(wavePage.context.window.DOOMOS_WAVES.hardware_validation.cpu_headroom,'unmeasured');
  assert.equal(wavePage.context.window.DOOMOS_WAVES.release_fix,true);
  assert.equal(wavePage.context.window.DOOMOS_WAVES.noise_freq_label_fix,true);
  const waveOverlay = api.fromBase64(wavePage.context.window.DOOMOS_WAVES_PATCH);
  const waveResult = api.applyPatch(waveOverlay, merged.bytes);
  assert.equal(waveResult.digest, wavePage.context.window.DOOMOS_WAVES.sha256);
  assert.equal(waveResult.bytes.length, wavePage.context.window.DOOMOS_WAVES.size);
  await choose(wavePage,stock);
  await wavePage.get('patch').listeners.click();
  assert.equal(digest(new Uint8Array(await wavePage.blob().arrayBuffer())),doom.digest,'default returns original DOOM');
  wavePage.get('reset').listeners.click();
  wavePage.get('synthPoly').checked = true;
  await choose(wavePage,stock);
  await wavePage.get('patch').listeners.click();
  assert.equal(digest(new Uint8Array(await wavePage.blob().arrayBuffer())),waveResult.digest);
  assert.match(wavePage.get('status').textContent,/Sound Generator \(4 voices, 17 new waves, envelopes, voice stealing, v9\.1\)/);
  assert.equal(wavePage.get('synthPoly').disabled,true);
  wavePage.get('reset').listeners.click();
  assert.equal(wavePage.get('synthPoly').disabled,false);
  assert.equal(wavePage.get('synthPoly').checked,true,'reset preserves chosen option');
  wavePage.get('synthPoly').checked = false;
  await choose(wavePage,stock);
  await wavePage.get('patch').listeners.click();
  assert.equal(digest(new Uint8Array(await wavePage.blob().arrayBuffer())),doom.digest);
  wavePage.get('reset').listeners.click();
  wavePage.get('synthPoly').checked = true;
  const corruptWave = waveOverlay.slice(); corruptWave[corruptWave.length-1] ^= 1;
  wavePage.context.window.DOOMOS_WAVES_PATCH = Buffer.from(corruptWave).toString('base64');
  await choose(wavePage,stock);
  await wavePage.get('patch').listeners.click();
  assert.equal(wavePage.blob(),null);
  assert.equal(wavePage.get('download').hidden,true);
  assert.equal(wavePage.get('synthPoly').disabled,false,'failed patch unlocks combined option');
  assert.doesNotMatch(fs.readFileSync(path.join(root,'README.md'),'utf8'),/<img[^>]*doom-os-512/);
  assert.doesNotMatch(html,/<img[^>]*doom-os/);
  console.log('PASS: single combined option, exact stock DOOM/poly-v4/v9.1 downloads, reset, header-image removal, hash gates and corrupt-overlay rejection.');
})().catch(error => { console.error(error); process.exitCode = 1; });
