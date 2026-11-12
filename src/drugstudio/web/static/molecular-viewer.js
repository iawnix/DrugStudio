/* Shared molecular scene and deterministic selection language. No eval or shell. */
(function (root) {
  'use strict';
  const amino = {ALA:'A',ARG:'R',ASN:'N',ASP:'D',CYS:'C',GLN:'Q',GLU:'E',GLY:'G',HIS:'H',ILE:'I',LEU:'L',LYS:'K',MET:'M',PHE:'F',PRO:'P',SER:'S',THR:'T',TRP:'W',TYR:'Y',VAL:'V',MSE:'M'};
  const atomKey = a => `${a.model}:${a.index}`;
  const residueKey = a => JSON.stringify([a.model, a.chain || '', a.resi, a.icode || '']);
  const polymer = a => Boolean(amino[a.resn]) || ['HID','HIE','HIP','CYX','ASH','GLH','LYN'].includes(a.resn);
  function select(expression, atoms, named = {}) {
    const tokens = String(expression).match(/"[^"]*"|'[^']*'|[()]|[^\s()]+/g) || [];
    let pos = 0;
    const peek = () => tokens[pos]?.toLowerCase();
    const take = () => { if (pos >= tokens.length) throw new Error('Incomplete selection'); return tokens[pos++]; };
    const all = new Set(atoms.map(atomKey));
    const filter = fn => new Set(atoms.filter(fn).map(atomKey));
    function primary() {
      const word = take(), key = word.toLowerCase();
      if (key === '(') { const s = or(); if (take() !== ')') throw new Error('Missing )'); return s; }
      if (key === 'not') { const s = primary(); return new Set([...all].filter(x => !s.has(x))); }
      if (key === 'byres') { const s = near(); const r = new Set(atoms.filter(a => s.has(atomKey(a))).map(residueKey)); return filter(a => r.has(residueKey(a))); }
      if (key === 'all') return all;
      if (key === 'none') return new Set();
      if (key === 'protein') return filter(polymer);
      if (key === 'hydrogen') return filter(a => ['H','D'].includes(a.elem));
      if (key === 'ligand') { if (named.ligand) return new Set(named.ligand); return atoms.some(a => a.model > 0) ? filter(a => a.model > 0) : filter(a => a.hetflag && !polymer(a) && !['HOH','WAT','NA','CL','CA','MG','ZN'].includes(a.resn)); }
      if (['chain','resi','resn','name','elem','model','alt'].includes(key)) {
        const value = take().replace(/^['"]|['"]$/g, ''), values = value.split('+');
        if (key === 'resi') {
          const ranges = values.map(v => { const m = v.match(/^(-?\d+)([A-Za-z]?)(?:-(-?\d+)([A-Za-z]?))?$/); if (!m) throw new Error('Invalid residue range'); return m; });
          return filter(a => ranges.some(m => {
            const n = Number(a.resi), code = a.icode || '';
            if (!m[3]) return n === Number(m[1]) && (!m[2] || code === m[2]);
            return n >= Number(m[1]) && n <= Number(m[3]) && (n !== Number(m[1]) || !m[2] || code >= m[2]) && (n !== Number(m[3]) || !m[4] || code <= m[4]);
          }));
        }
        const prop = {name:'atom',alt:'altLoc'}[key] || key;
        return filter(a => values.includes(String(a[prop] ?? '')));
      }
      if (Object.prototype.hasOwnProperty.call(named, word)) return new Set(named[word]);
      throw new Error(`Unsupported selection: ${word}`);
    }
    function near() {
      let s = primary();
      if (peek() === 'within') {
        take(); const distance = Number(take());
        if (!Number.isFinite(distance) || distance < 0 || distance > 100) throw new Error('Distance must be 0–100 Å');
        if (take().toLowerCase() !== 'of') throw new Error('Expected of');
        const other = primary(), cell = distance || 1, grid = new Map();
        atoms.filter(a => other.has(atomKey(a))).forEach(a => { const key = [a.x,a.y,a.z].map(v => Math.floor(v/cell)).join(','); if (!grid.has(key)) grid.set(key,[]); grid.get(key).push(a); });
        s = filter(a => {
          if (!s.has(atomKey(a))) return false;
          const [x,y,z] = [a.x,a.y,a.z].map(v => Math.floor(v/cell));
          for (let i=-1;i<=1;i++) for (let j=-1;j<=1;j++) for (let k=-1;k<=1;k++) {
            if ((grid.get([x+i,y+j,z+k].join(',')) || []).some(b => (a.x-b.x)**2+(a.y-b.y)**2+(a.z-b.z)**2 <= distance**2)) return true;
          }
          return false;
        });
      }
      return s;
    }
    function and() { let s = near(); while (peek() === 'and') { take(); const b = near(); s = new Set([...s].filter(x => b.has(x))); } return s; }
    function or() { let s = and(); while (peek() === 'or') { take(); s = new Set([...s,...and()]); } return s; }
    const result = or(); if (pos !== tokens.length) throw new Error(`Unexpected token: ${tokens[pos]}`); return result;
  }
  const styles = {
    cartoon: {cartoon:{color:'spectrum'}}, sticks: {stick:{radius:.18,colorscheme:'Jmol'}},
    lines: {line:{colorscheme:'Jmol'}}, ball: {stick:{radius:.15,colorscheme:'Jmol'},sphere:{scale:.28,colorscheme:'Jmol'}}
  };
  class Scene {
    constructor(host) {
      this.host = host; host.innerHTML = '';
      this.viewer = root.$3Dmol.createViewer(host, {backgroundColor:getComputedStyle(document.documentElement).getPropertyValue('--surface-2').trim(),antialias:true});
      this.models = []; this.siteMode = false; this.named = {}; this.overlays = []; this.selection = new Set(); this.style = 'cartoon'; this.color = 'orange'; this.surfaceGeneration = 0; this.labels = []; this.measurements = [];
      this.resize = new ResizeObserver(() => { if (host.clientWidth && host.clientHeight) this.viewer.resize(); }); this.resize.observe(host);
    }
    add(text, format, style = 'cartoon') {
      const model = this.viewer.addModel(text, format, {altLoc:'*'});
      // Keep explicit handles: 3Dmol.getModel(missingId) returns the LAST
      // model, so asking for an absent ligand can accidentally restyle protein.
      if (!this.models.length) this.style = style;
      this.models.push(model);
      model.selectedAtoms({}).forEach(atom => {
        for (const field of ['chain','icode','altLoc']) atom[field] = String(atom[field] || '').trim();
      });
      model.setStyle({}, styles[style]); this.bindPicking(); return model;
    }
    atoms() { return this.viewer.selectedAtoms({}); }
    selected() { return this.atoms().filter(a => this.selection.has(atomKey(a))); }
    bindPicking() {
      this.viewer.setClickable({}, true, (atom, viewer, event) => {
        const r = residueKey(atom), ids = this.atoms().filter(a => residueKey(a) === r).map(atomKey);
        const next = event?.ctrlKey || event?.metaKey ? new Set(this.selection) : new Set();
        const remove = ids.every(x => next.has(x)); ids.forEach(x => remove ? next.delete(x) : next.add(x)); this.setSelection(next);
      });
    }
    eachSelection(ids, callback) {
      // Apply styles by residue selectors. 3Dmol's low-level atom-index
      // selector is version-dependent; residue selectors are stable and keep
      // the highlighted sequence and structure in sync.
      const seen = new Set();
      this.atoms().filter(a => ids.has(atomKey(a))).forEach(a => {
        const key = residueKey(a); if (seen.has(key)) return; seen.add(key);
        callback({model:a.model,chain:a.chain,resi:a.resi,icode:a.icode || ''});
      });
    }
    setSelection(ids) { this.selection = new Set(ids); this.paint(); this.onSelection?.(this.selected()); }
    paint() {
      this.models[0]?.setStyle({}, this.siteMode ? {cartoon:{color:'#aab4bf',opacity:.3}} : styles[this.style] || styles.cartoon);
      this.models.slice(1).forEach(model => model.setStyle({}, styles.ball));
      this.overlays.forEach(layer => this.eachSelection(new Set(layer.ids), sel => this.viewer.addStyle(sel, layer.style)));
      this.eachSelection(this.selection, sel => this.viewer.addStyle(sel, {cartoon:{color:this.color,opacity:1},stick:{colorscheme:'Jmol',radius:.24}}));
      this.viewer.render();
    }
    focus() { if (this.selection.size) { const atoms = this.selected(); this.viewer.zoomTo({predicate:a => atoms.includes(a)}); } else this.viewer.zoomTo(); if (this.host.clientWidth > 0 && this.host.clientWidth < this.host.clientHeight) this.viewer.zoom(this.host.clientWidth / this.host.clientHeight); this.viewer.render(); }
    async surface(show, opacity = .5) {
      const version = ++this.surfaceGeneration;
      this.viewer.removeAllSurfaces(); this.surfaceOn = show;
      if (show) {
        const id = await this.viewer.addSurface(root.$3Dmol.SurfaceType.VDW, {opacity,color:'#8daec4'}, {model:0});
        if (version !== this.surfaceGeneration) return;
      }
      this.viewer.render();
    }
    command(line) {
      const m = line.trim().match(/^(select|show|color|zoom)\s+(.+)$/i);
      if (!m) { this.setSelection(select(line, this.atoms(), this.named)); return; }
      const op = m[1].toLowerCase(), parts = m[2].split(',').map(s => s.trim());
      if (op === 'select') {
        if (parts.length !== 2 || !/^[A-Za-z_][\w]*$/.test(parts[0]) || ['all','none','protein','hydrogen','chain','resi','resn','name','elem','model','alt','not','byres'].includes(parts[0])) throw new Error('Use select name, expression');
        const ids = select(parts[1], this.atoms(), this.named); this.named[parts[0]] = ids; this.setSelection(ids);
      } else if (op === 'zoom') { this.setSelection(select(m[2], this.atoms(), this.named)); this.focus(); }
      else {
        if (parts.length !== 2) throw new Error(`Use ${op} value, selection`);
        const ids = select(parts[1], this.atoms(), this.named);
        if (op === 'color') { if (!/^#[\da-f]{6}$/i.test(parts[0]) && !['orange','red','green','blue','yellow','cyan','magenta','white','gray'].includes(parts[0])) throw new Error('Unsupported color'); this.color = parts[0]; this.setSelection(ids); }
        else { const style = parts[0] === 'stick' ? 'sticks' : parts[0]; if (!styles[style]) throw new Error('Use cartoon, sticks, lines or ball'); this.setSelection(ids); this.overlays.push({ids:[...ids],style:styles[style]}); this.paint(); }
      }
    }
    residues() { const out = new Map(); this.atoms().filter(a => a.model === 0 && polymer(a)).forEach(a => { const key = residueKey(a); if (!out.has(key)) out.set(key, {key,chain:a.chain || '',resi:a.resi,icode:a.icode || '',resn:a.resn,letter:amino[a.resn] || 'X',ids:[]}); out.get(key).ids.push(atomKey(a)); }); return [...out.values()]; }
    candidateBox(padding) {
      const atoms = this.selected().filter(a => a.model === 0 && !['H','D'].includes(a.elem));
      if (!atoms.length) throw new Error('Select receptor atoms first');
      if (!Number.isFinite(padding) || padding < 0 || padding > 30) throw new Error('Padding must be 0–30 Å');
      const lo = ['x','y','z'].map(k => atoms.reduce((n,a) => Math.min(n,a[k]),Infinity)), hi = ['x','y','z'].map(k => atoms.reduce((n,a) => Math.max(n,a[k]),-Infinity));
      return {center:lo.map((v,i) => (v+hi[i])/2),size:lo.map((v,i) => Math.max(1,hi[i]-v+2*padding))};
    }
    labelSelection() { this.labels.forEach(l => this.viewer.removeLabel(l)); this.labels = []; const seen = new Set(); this.selected().forEach(a => { const key = residueKey(a); if (seen.has(key)) return; seen.add(key); this.labels.push(this.viewer.addLabel(`${a.chain || '∅'}:${a.resi}${a.icode || ''} ${a.resn || ''}`, {position:a,fontSize:12,backgroundOpacity:.65})); }); this.viewer.render(); }
    distance() { const atoms = this.selected(); if (atoms.length !== 2) throw new Error('Select exactly two atoms (for example name CA and resi 10+20)'); const [a,b] = atoms; const d = Math.hypot(a.x-b.x,a.y-b.y,a.z-b.z); this.measurements.push(this.viewer.addLine({start:a,end:b,dashed:true,color:'orange'})); this.labels.push(this.viewer.addLabel(`${d.toFixed(2)} Å`,{position:{x:(a.x+b.x)/2,y:(a.y+b.y)/2,z:(a.z+b.z)/2},fontSize:12})); this.viewer.render(); return d; }
    save() { return {selection:[...this.selection],siteMode:this.siteMode,overlays:this.overlays,named:Object.fromEntries(Object.entries(this.named).map(([k,v]) => [k,[...v]])),style:this.style,color:this.color,view:this.viewer.getView()}; }
    restore(data) { if (!data) return; this.siteMode = Boolean(data.siteMode); this.overlays = data.overlays || []; this.named = Object.fromEntries(Object.entries(data.named || {}).map(([k,v]) => [k,new Set(v)])); this.style = styles[data.style] ? data.style : 'cartoon'; this.color = data.color || 'orange'; this.setSelection(new Set(data.selection || [])); if (Array.isArray(data.view)) this.viewer.setView(data.view); this.viewer.render(); }
    dispose() { ++this.surfaceGeneration; this.resize.disconnect(); this.viewer.clear(); this.models = []; }
  }
  root.DrugStudioMolecule = {Scene,select,atomKey,residueKey,styles};
  if (typeof module !== 'undefined') module.exports = root.DrugStudioMolecule;
})(typeof window === 'undefined' ? globalThis : window);
