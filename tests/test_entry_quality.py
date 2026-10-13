"""Saved-readme entry reuse and URL-only output, all synthetic/offline."""
import copy
import unittest
from unittest import mock
from learnradar.readme import analyze_readme_entry
from learnradar.collector import collect_repository
from learnradar.analysis import _learning_entry_block
from tests.test_collector import FakeClient
import tests.test_validate as validate_fixture

README='''# Fixture notebooks
## Setup
[Open notebook](https://colab.research.google.com/github/example/demo/blob/main/setup.ipynb)
1. Obtain an API key from the documented provider.
2. Add API_KEY to notebook secrets and enable access.
3. Open the notebook and run top to bottom.
Running locally: `pip install -r requirements.txt`, open with Jupyter.
'''

class EntryQualityTests(unittest.TestCase):
    def test_notebook_steps_are_signal_without_code_fences(self):
        self.assertTrue(analyze_readme_entry(README,'example/demo')['example_signal'])
        for text in (README.replace('example/demo','unrelated/repo'),README.replace('run top to bottom','read the overview'),README.split('1.')[0]):
            self.assertFalse(analyze_readme_entry(text,'example/demo')['example_signal'])

    def test_reuses_retained_readme_without_extra_requests_or_slots(self):
        client=FakeClient()
        with mock.patch.object(client,'get_readme',return_value=(README,{'path':'README.md','html_url':'https://github.com/example/demo#readme'})), mock.patch('learnradar.collector._fetch_page') as fetch:
            saved=collect_repository(client,'example/demo',role='target',max_sources=3,fetch_learning_entry=True)
        fetch.assert_not_called()
        source=next(x for x in saved.evidence if x.kind=='github_readme')
        self.assertTrue(saved.learning_entry['verified'])
        self.assertTrue(saved.learning_entry['example_signal'])
        self.assertEqual(saved.learning_entry['url'],source.url)
        self.assertEqual(saved.learning_entry['evidence_id'],source.evidence_id)
        self.assertEqual(len(saved.evidence),3)

    def test_prompt_keeps_gap_out_of_entry_and_false_completion_optional(self):
        for entry in (None,{'url':'https://example.com/docs','verified':True,'example_signal':False}, {'url':'https://example.com/docs','verified':False}):
            block=_learning_entry_block({'learning_entry':entry})
            self.assertIn('executable=false',block)
            self.assertIn('entry',block)
            self.assertIn('done_criteria允许为空',block)
            self.assertNotIn('entry 必须写成“暂不可执行”',block)

    def test_url_with_gap_text_is_rejected_but_false_empty_completion_passes(self):
        case=validate_fixture.ValidateReportTests();case.setUp()
        report=copy.deepcopy(case.report)
        report['learning_action'].update(executable=False,done_criteria='',entry='https://example.com/docs（缺少步骤）')
        self.assertTrue(any('只能是明确' in x for x in case.check(report).errors))
        report['learning_action']['entry']='https://github.com/owner/target#readme'
        self.assertTrue(case.check(report).ok)

    def test_unclear_novelty_cannot_bypass_evidence_quality_gate(self):
        case=validate_fixture.ValidateReportTests();case.setUp()
        report=copy.deepcopy(case.report)
        report['novelty'].update(what='证据不足',when='不确定',kind='unclear',evidence_ids=[],quote='')
        result=case.check(report,require_quotes=True)
        self.assertFalse(result.ok)
        self.assertTrue(any('novelty.evidence_ids' in x for x in result.errors))
        self.assertTrue(any('novelty.quote' in x for x in result.errors))

if __name__=='__main__':unittest.main()
