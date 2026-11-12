"""Real 3Dmol selection and task-to-pose integration, using the shared harness."""
import json
from test_review_ui import BrowserHarness


class MolecularUI(BrowserHarness):
    def controls(self):
        self.open_structure()
        self.page.wait_for_selector('#structure-sequence [data-residue]')
        self.page.locator('.molecule-tools > summary').first.click()

    def expression(self, value):
        self.page.fill('#selection-expression',value)
        self.page.locator('#selection-form button').click()

    def test_sequence_expression_and_saved_view(self):
        self.controls()
        self.page.click('[data-residue="0"]')
        self.assertIn('1 个残基', self.page.inner_text('#viewer-hint'))
        self.page.click('[data-residue="1"]',modifiers=['Shift'])
        self.assertEqual(self.page.locator('.sequence-chain button.selected').count(),2)
        self.expression('chain A and resi 20 and name CA')
        self.assertIn('1 原子', self.page.inner_text('#viewer-hint'))
        selected = self.page.evaluate("window.__reviewViewer.selectedAtoms({model:0}).filter(a=>a.style.stick?.colorscheme==='Jmol').map(a=>a.resi)")
        self.assertEqual(selected,[20,20,20,20])
        self.page.click('#selection-save'); self.page.click('#selection-clear'); self.page.click('#selection-restore')
        self.assertIn('1 原子',self.page.inner_text('#viewer-hint'))
        self.expression('name CA and resi 10+20')
        self.page.click('#selection-distance')
        self.assertIn('30.00',self.page.inner_text('#selection-message'))
        self.expression('chain X')
        self.assertIn('0 原子',self.page.inner_text('#viewer-hint'))

    def test_selection_box_is_draft_until_applied(self):
        self.controls(); self.expression('chain A and resi 10')
        self.page.get_by_text('将选择应用到对接盒',exact=True).click()
        self.page.click('#selection-box')
        self.assertEqual(self.manifest()['human_decisions'],[])
        self.assertEqual(self.page.evaluate('window.__reviewViewer.__testLines.size'),12)
        self.page.fill('#selection-reason','Selected observed residue A:10')
        with self.page.expect_response(lambda r: '/api/review' in r.url and r.request.method == 'POST') as pending:
            self.page.click('#selection-apply')
        self.assertEqual(pending.value.status,200,pending.value.text())
        decision = self.manifest()['human_decisions'][-1]
        self.assertEqual(decision['action'],'override')
        self.assertIn('Viewer selection',decision['reason'])
        self.assertIn('a1',decision['reason'])

    def test_surface_layer_and_style_keep_box(self):
        self.controls(); self.page.click('#toggle-box')
        self.page.check('#molecule-surface')
        self.page.wait_for_function("Object.keys(window.__reviewViewer.surfaces).length > 0 && document.querySelector('#surface-status').textContent === ''")
        self.page.select_option('#molecule-style','lines')
        self.assertEqual(self.page.evaluate('window.__reviewViewer.__testLines.size'),12)
        self.assertGreater(self.page.evaluate('Object.keys(window.__reviewViewer.surfaces).length'),0)
        self.page.uncheck('#molecule-surface')
        self.page.wait_for_function('Object.keys(window.__reviewViewer.surfaces).length === 0')

    def test_result_opens_exact_receptor_and_second_pose(self):
        manifest = self.manifest(); art = manifest['artifacts']['structure'][0]
        art['kind'] = 'prepared_receptor_pdb'
        (self.run_root/'meta.json').write_text(json.dumps({'pdbqt':{'sha256':'qt'},'prepared_pdb':{'sha256':art['sha256']}}))
        (self.run_root/'t').mkdir()
        (self.run_root/'t/result.json').write_text(json.dumps({'status':'complete','poses':[
            {'mode':1,'affinity_kcal_mol':-5,'qc_pass':True}, {'mode':2,'affinity_kcal_mol':-4,'qc_pass':True}]}))
        def sdf(x):
            return f'pose\n  test\n\n  1  0  0  0  0  0  0  0  0  0999 V2000\n{x:10.4f}{0:10.4f}{0:10.4f} C   0  0  0  0  0  0  0  0  0  0  0  0\nM  END\n$$$$\n'
        (self.run_root/'t/poses.sdf').write_text(sdf(4)+sdf(8))
        manifest['artifacts']['docking']=[
            {'artifact_id':'qt','kind':'prepared_receptor_pdbqt','sha256':'qt','path':'q.pdbqt','metadata':{'metadata_relpath':'meta.json'}},
            {'artifact_id':'record','kind':'docking_output','path':'t/result.json','metadata':{'relative_output':'t/result.json'}},
            {'artifact_id':'poses','kind':'docking_output','path':'t/poses.sdf','metadata':{'relative_output':'t/poses.sdf'}}]
        manifest['tasks']=[{'task_id':'t','status':'succeeded','ligand_id':'lig-1','receptor_id':'T1','engine':'vina','input_artifact_ids':['qt'],'output_artifact_ids':['record','poses']}]
        self.write_manifest(manifest)
        self.page.click('[data-view="results"]')
        self.assertIn('1/1', self.page.inner_text('#metric-row'))
        self.page.click('[data-result-row="0"]'); self.page.select_option('#result-pose-choice','1'); self.page.click('#result-open-pose')
        self.page.wait_for_function('window.__reviewViewer?.getModel(1)?.selectedAtoms({}).length === 1')
        self.assertEqual(self.page.evaluate('window.__reviewViewer.getModel(1).selectedAtoms({})[0].x'),8)
        self.assertEqual(self.page.input_value('#pose-select'),'t:1')
        self.page.click('#refresh-button')
        self.page.wait_for_timeout(200)
        self.assertEqual(self.page.input_value('#pose-select'),'t:1')

    def test_overall_view_uses_cartoon_and_site_button_is_actionable(self):
        self.open_structure()
        model_style = self.page.evaluate("window.__reviewViewer.getModel(0).selectedAtoms({}).map(a => Object.keys(a.style || {}))")
        self.assertTrue(all('cartoon' in styles and 'stick' not in styles for styles in model_style))
        self.page.click('[data-protein-style="site"]')
        self.page.wait_for_function("document.querySelector('#viewer-hint').textContent.includes('残基')")
        self.assertGreater(self.page.evaluate("window.__reviewViewer.selectedAtoms({model:0}).filter(a => a.style?.stick).length"),0)
        self.page.click('[data-protein-style="cartoon"]')
        self.assertTrue(self.page.evaluate("window.__reviewViewer.getModel(0).selectedAtoms({}).every(a => a.style?.cartoon && !a.style?.stick)"))

    def test_sticks_keep_element_colors_when_residues_are_selected(self):
        self.open_structure()
        self.page.locator('.molecule-tools > summary').first.click()
        self.page.select_option('#molecule-style', 'sticks')
        self.page.click('[data-residue="0"]')
        colors = self.page.evaluate('''() => {
          const atoms = window.__reviewViewer.getModel(0).selectedAtoms({});
          return atoms.filter(a => ['N','C','O'].includes(a.elem)).map(a => ({elem:a.elem, color:a.style?.stick?.color, scheme:a.style?.stick?.colorscheme}));
        }''')
        self.assertTrue(colors)
        self.assertTrue(all(item['scheme'] == 'Jmol' and item['color'] is None for item in colors))
        self.assertGreaterEqual(len({item['elem'] for item in colors}), 3)
