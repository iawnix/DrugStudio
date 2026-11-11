import json
import tempfile
import unittest
from pathlib import Path
from drugstudio.web.results import presentation


class ResultPresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/'meta.json').write_text(json.dumps({'pdbqt':{'sha256':'qt'},'prepared_pdb':{'sha256':'pdb'}}))
        (self.root/'result.json').write_text(json.dumps({'status':'complete','poses':[
            {'mode':1,'affinity_kcal_mol':-20,'qc_pass':False},
            {'mode':2,'affinity_kcal_mol':0,'qc_pass':True}]}))
        self.artifacts = [
            {'artifact_id':'qt','kind':'prepared_receptor_pdbqt','sha256':'qt','metadata':{'metadata_relpath':'meta.json'}},
            {'artifact_id':'pdb','kind':'prepared_receptor_pdb','sha256':'pdb','path':'receptor.pdb'},
            {'artifact_id':'result','kind':'docking_output','path':'result.json','metadata':{'relative_output':'t/result.json'}},
            {'artifact_id':'pose','kind':'docking_output','path':'t/poses.sdf','metadata':{'relative_output':'t/poses.sdf'}}]
        # The record may be stored in a content-addressed directory; the suffix
        # is the workflow artifact's published filename.
        (self.root/'t').mkdir(); (self.root/'result.json').rename(self.root/'t/result.json')
        self.artifacts[2]['path']='t/result.json'
        self.manifest = {'tasks':[{'task_id':'t','status':'succeeded','engine':'vina','ligand_id':'L',
                                  'input_artifact_ids':['qt'],'output_artifact_ids':['result','pose']}],
                         'artifacts':{'all':self.artifacts}}

    def test_normalization_qc_zero_and_exact_receptor_link(self):
        data = presentation(self.root,self.manifest)
        task = data['tasks'][0]
        self.assertEqual(data['summary']['succeeded'],1)
        self.assertEqual(task['score'],0)
        self.assertEqual(task['best_pose']['record_index'],1)
        self.assertEqual(task['receptor_artifact']['artifact_id'],'pdb')
        self.assertTrue(task['viewable'])
        self.assertEqual(task['qc_pass_count'],1)

    def test_unknown_identity_never_uses_first_structure(self):
        self.artifacts[1]['sha256']='different'
        task = presentation(self.root,self.manifest)['tasks'][0]
        self.assertFalse(task['viewable'])
        self.assertIsNone(task['receptor_artifact'])

    def test_attempts_and_engines_are_not_pooled(self):
        self.manifest['stage_results']={'docking':{'metadata':{'attempt':2}}}
        task = presentation(self.root,self.manifest,{'docking_attempt':1,'tasks':[{'task_id':'t','score':-100}]})['tasks'][0]
        self.assertIsNone(task['score'])
        self.assertEqual(task['poses'],[])
        self.manifest['tasks'].append({'task_id':'other','engine':'other','score':-50,'status':'completed'})
        summary = presentation(self.root,self.manifest)['summary']
        self.assertEqual(len(summary['groups']),2)
        self.assertIsNone(summary['best_kcal_mol'])

    def test_missing_score_stays_missing(self):
        self.manifest['artifacts']={}
        self.assertIsNone(presentation(self.root,self.manifest)['tasks'][0]['score'])

    def test_record_cannot_escape_run(self):
        self.artifacts[2]['path']='../t/result.json'
        self.assertEqual(presentation(self.root,self.manifest)['tasks'][0]['poses'],[])

    def test_invalidated_report_does_not_resurrect_cleared_tasks(self):
        self.manifest['tasks']=[]
        self.manifest['stage_results']={'assessment':{'status':'stale'}}
        data = presentation(self.root,self.manifest,{'tasks':[{'task_id':'t','status':'complete','score':-9}]})
        self.assertEqual(data['tasks'],[])
        self.assertTrue(data['stale'])
