import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from drugstudio.contracts import LigandSpec, TargetSpec
from drugstudio.storage import ArtifactStore
from drugstudio.ports import ToolContext, ToolResult
from drugstudio.integrations.pdbfixer_bridge import _filtered_pdb, _label
from drugstudio.integrations.receptor_pipeline import PDBFixerReceptorPreparer, ReceptorPreparationPolicy
from drugstudio.integrations.ligand_pipeline import LigandPreparationPipeline


def atom(serial, name, residue, number):
    return f'HETATM{serial:5d} {name:^4} {residue:3s} A{number:4d}    {float(serial):8.3f}{0.:8.3f}{0.:8.3f}{1.:6.2f}{0.:6.2f}          {name[0]:>2}  '


class PreparationRegressions(unittest.TestCase):
    def test_openmm_blank_insertion_code_matches_site_label(self):
        residue=SimpleNamespace(chain=SimpleNamespace(id='A'), id='99', insertionCode=' ')
        self.assertEqual(_label(residue),'A:99')
        residue.insertionCode='B'
        self.assertEqual(_label(residue),'A:99B')

    def test_polymer_hetatm_survives_and_stale_connections_are_removed(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d)/'in.pdb', Path(d)/'out.pdb'
            src.write_text('\n'.join([atom(1,'N','MSE',1),atom(2,'CA','MSE',1),atom(3,'C','MSE',1),
                                     atom(4,'C1','BNZ',2),'CONECT    1    2    4','CONECT    4    1','END'])+'\n')
            m = _filtered_pdb(src,dst,{})
            self.assertIn('MSE',dst.read_text())
            self.assertNotIn('BNZ',dst.read_text())
            self.assertIn('CONECT    1    2\n',dst.read_text())
            self.assertNotIn('CONECT    4',dst.read_text())
            self.assertEqual(len(m['protected_polymer_heterogens']),1)

    def test_keep_all_policy_is_respected(self):
        with tempfile.TemporaryDirectory() as d:
            src,dst=Path(d)/'in.pdb',Path(d)/'out.pdb'
            src.write_text(atom(1,'C1','BNZ',2)+'\n')
            m=_filtered_pdb(src,dst,{'remove_heterogens':False})
            self.assertIn('BNZ',dst.read_text())
            self.assertEqual(m['removed_heterogens'],[])

    def test_optimized_coordinates_feed_next_stage(self):
        with tempfile.TemporaryDirectory() as d:
            store=ArtifactStore(d)
            optimized=store.publish_bytes(relative_path='optimized.sdf',content=b'optimized',kind='conformer_optimized_sdf')
            initial=store.publish_bytes(relative_path='initial.sdf',content=b'initial',kind='conformer_initial_sdf')
            class Optimizer:
                def prepare(self,ligand,context):
                    return ToolResult(status='succeeded',artifacts=[optimized,initial])
            class Capture:
                def prepare(self,ligand,context):
                    self.path=ligand.input_path
                    return ToolResult(status='succeeded')
            capture=Capture()
            result=LigandPreparationPipeline(store,(Optimizer(),capture)).prepare(
                LigandSpec(ligand_id='benzene',smiles='c1ccccc1'),ToolContext('test',Path(d)/'work'))
            self.assertEqual(result.status,'succeeded')
            self.assertEqual(capture.path,str(store.path(optimized.path)))

    def test_reuse_rejects_changed_policy_or_output(self):
        with tempfile.TemporaryDirectory() as d:
            store=ArtifactStore(d)
            source=store.publish_bytes(relative_path='input.pdb',content=b'ATOM  input\n',kind='experimental_structure')
            context=ToolContext('test',Path(d)/'work/repair')
            target=TargetSpec(target_id='test')
            def worker(command,**kwargs):
                wd=kwargs['cwd']
                (wd/'repaired_receptor.pdb').write_text('ATOM  repaired\n')
                (wd/'receptor_repair.json').write_text('{}\n')
                kwargs['log_path'].write_text('done\n')
            preparer=PDBFixerReceptorPreparer(store)
            with patch('drugstudio.integrations.receptor_pipeline._run',side_effect=worker) as run:
                self.assertEqual(preparer.prepare(source,target,context).status,'succeeded')
                self.assertEqual(preparer.prepare(source,target,context).status,'succeeded')
                self.assertEqual(run.call_count,1)
                preparer.policy=replace(preparer.policy,ph=6.)
                result=preparer.prepare(source,target,context)
                self.assertEqual(result.status,'failed')
                self.assertIn('reuse rejected',result.error)
                self.assertEqual(json.loads((context.workdir/'policy.json').read_text())['ph'],7.4)
                preparer.policy=ReceptorPreparationPolicy()
                (context.workdir/'repaired_receptor.pdb').write_text('tampered')
                self.assertEqual(preparer.prepare(source,target,context).status,'failed')


if __name__ == '__main__':
    unittest.main()
