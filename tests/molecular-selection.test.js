const {test} = require('node:test');
const assert = require('node:assert/strict');
const {select,atomKey,Scene} = require('../src/drugstudio/web/static/molecular-viewer.js');
const atoms = [
 {model:0,index:0,chain:'',resi:-1,icode:'A',resn:'ASP',atom:'CA',elem:'C',x:0,y:0,z:0},
 {model:0,index:1,chain:'',resi:-1,icode:'A',resn:'ASP',atom:'CB',elem:'C',x:1,y:0,z:0},
 {model:0,index:2,chain:'AB',resi:10,icode:'',resn:'GLU',atom:'CA',elem:'C',x:10,y:0,z:0},
 {model:0,index:3,chain:'AB',resi:10,icode:'A',resn:'GLU',atom:'CA',elem:'C',x:11,y:0,z:0},
 {model:1,index:0,hetflag:true,resn:'LIG',atom:'C1',elem:'C',x:3,y:0,z:0},
];
const ids = expression => [...select(expression,atoms)];
test('residue identifiers, blank/multi-character chains and insertion codes', () => {
 assert.deepEqual(ids('chain "" and resi -1A'),['0:0','0:1']);
 assert.deepEqual(ids('chain AB and resi 10A'),['0:3']);
 assert.deepEqual(ids('resi -1-10 and name CA'),['0:0','0:2','0:3']);
 assert.deepEqual(ids('protein and not (chain AB or name CB)'),['0:0']);
});
test('distance and whole-residue expansion retain atom/model identity', () => {
 assert.deepEqual(ids('byres (protein within 2 of ligand)'),['0:0','0:1']);
 assert.deepEqual([...select('site and name CA',atoms,{site:select('chain AB',atoms)})],['0:2','0:3']);
 assert.deepEqual(ids('model 1'),['1:0']);
});
test('unsupported syntax fails rather than becoming arbitrary code or selecting everything', () => {
 for (const s of ['all; fetch(x)','resi abc','chain','foo','protein within NaN of ligand','(protein','select x, all']) assert.throws(() => ids(s));
});
test('box uses selected receptor atoms only and validates padding', () => {
 const scene = {selected:() => atoms};
 assert.deepEqual(Scene.prototype.candidateBox.call(scene,5),{center:[5.5,0,0],size:[21,10,10]});
 assert.throws(() => Scene.prototype.candidateBox.call(scene,-1));
 assert.throws(() => Scene.prototype.candidateBox.call({selected:() => [atoms[4]]},5));
});
