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
      id, checked: id === 'synthPoly', hidden: false, disabled: false, value: '', listeners: {},
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
  assert.match(page.get('status').textContent, /experimental poly-test-v4/);

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
  assert.equal(wavePage.get('synthWaves').checked,false,'candidate must not be selected by default');
  assert.doesNotMatch(wavePage.get('doomos-patcher').innerHTML,/id="synthWaves" checked/);
  assert.equal(wavePage.context.window.DOOMOS_WAVES.revision,'v8-ram');
  assert.equal(wavePage.context.window.DOOMOS_WAVES.sha256,'12dbeae4c85722e3109aa2359102e8525c0c5e3729fb64126456abd83ae283c7');
  assert.equal(wavePage.context.window.DOOMOS_WAVES.size,2961072);
  assert.equal(wavePage.context.window.DOOMOS_WAVES.execution,'sdram-gap');
  assert.equal(wavePage.context.window.DOOMOS_WAVES.pool_unchanged_from_v4,true);
  assert.equal(wavePage.context.window.DOOMOS_WAVES.polyblep.length,0);
  assert.match(wavePage.context.window.DOOMOS_WAVES.duty,/raw.*no smoothing/);
  assert.equal(wavePage.context.window.DOOMOS_WAVES.status,'hardware-unverified');
  assert.equal(wavePage.context.window.DOOMOS_WAVES.release_fix,true);
  assert.equal(wavePage.context.window.DOOMOS_WAVES.noise_freq_label_fix,true);
  const waveOverlay = api.fromBase64(wavePage.context.window.DOOMOS_WAVES_PATCH);
  const waveResult = api.applyPatch(waveOverlay, merged.bytes);
  assert.equal(waveResult.digest, wavePage.context.window.DOOMOS_WAVES.sha256);
  assert.equal(waveResult.bytes.length, wavePage.context.window.DOOMOS_WAVES.size);
  await choose(wavePage,stock);
  await wavePage.get('patch').listeners.click();
  assert.equal(digest(new Uint8Array(await wavePage.blob().arrayBuffer())),merged.digest,'default returns working v4');
  wavePage.get('reset').listeners.click();
  wavePage.get('synthWaves').checked = true;
  await choose(wavePage,stock);
  await wavePage.get('patch').listeners.click();
  assert.equal(digest(new Uint8Array(await wavePage.blob().arrayBuffer())),waveResult.digest);
  assert.match(wavePage.get('status').textContent,/17 Wave Lab types/);
  assert.equal(wavePage.get('synthWaves').disabled,true);
  wavePage.get('reset').listeners.click();
  wavePage.get('synthWaves').checked = false;
  await choose(wavePage,stock);
  await wavePage.get('patch').listeners.click();
  assert.equal(digest(new Uint8Array(await wavePage.blob().arrayBuffer())),merged.digest);
  wavePage.get('reset').listeners.click();
  wavePage.get('synthWaves').checked = true;
  wavePage.get('synthPoly').checked = false;
  wavePage.get('synthPoly').listeners.change();
  assert.equal(wavePage.get('synthWaves').disabled,true);
  await choose(wavePage,stock);
  await wavePage.get('patch').listeners.click();
  assert.equal(digest(new Uint8Array(await wavePage.blob().arrayBuffer())),doom.digest);
  wavePage.get('reset').listeners.click();
  wavePage.get('synthPoly').checked = true;
  wavePage.get('synthPoly').listeners.change();
  assert.equal(wavePage.get('synthWaves').disabled,false);
  const corruptWave = waveOverlay.slice(); corruptWave[corruptWave.length-1] ^= 1;
  wavePage.context.window.DOOMOS_WAVES_PATCH = Buffer.from(corruptWave).toString('base64');
  await choose(wavePage,stock);
  await wavePage.get('patch').listeners.click();
  assert.equal(wavePage.blob(),null);
  assert.equal(wavePage.get('download').hidden,true);
  console.log('PASS: shipped page/applier, stock DOOM, poly-v4 and 17-wave downloads, option dependency/reset, hash gates and corrupt-overlay rejection.');
})().catch(error => { console.error(error); process.exitCode = 1; });
