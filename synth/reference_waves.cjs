// Evaluate the original Wave Lab recipes only, without running its browser UI.
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const start = html.indexOf('  const TAU =');
const end = html.indexOf('  const BY_ID =');
if (start < 0 || end <= start) throw new Error('Wave Lab source layout changed');
const context = vm.createContext({});
vm.runInContext(html.slice(start, end) + '\nglobalThis.types = TYPES;', context);
const types = context.types.filter(type => type.fam !== 'stock');
const cases = [];
const duties = process.argv.includes('--all-duty') ? Array.from({length:101},(_,i)=>i/100) : [0,0.25,0.5,0.75,1];
types.forEach((type, i) => {
  for (const p of [0, 0.123, 0.271, 0.4999, 0.5001, 0.733, 0.937, 0.99999]) {
    for (const d of duties) {
      for (const dt of [0.001, 0.009, 0.04]) {
        const r = p < 0.5 ? -0.7 : 0.3;
        cases.push({type: 15+i, p, d, dt, r, value: type.fn(p, d, dt, 48000, r)});
      }
    }
  }
});
process.stdout.write(JSON.stringify({ids: types.map(type => type.id), cases}));
