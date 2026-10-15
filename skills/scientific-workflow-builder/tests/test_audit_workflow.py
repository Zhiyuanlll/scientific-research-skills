import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'audit_workflow.py'


class AuditWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'project'
        self.root.mkdir()
        self.spec = {'schema_version': 1, 'workflow_id': 'example', 'stages': [self.stage('a'), self.stage('b', ['a']), self.stage('c', ['b']), self.stage('independent')]}
        for filename, content in [('config.json', '{}'), ('code.py', 'print(1)'), ('result.dat', '1.0')]:
            (self.root / filename).write_text(content, encoding='utf-8')

    def stage(self, key, deps=None):
        return {'id': key, 'depends_on': deps or [], 'role': 'validation', 'acceptance': [{'id': 'tolerance', 'description': 'Declared tolerance met'}], 'outputs': [{'id': 'result', 'description': 'Observed result', 'units': None}]}

    def claim(self, filename):
        return {'path': filename, 'sha256': hashlib.sha256((self.root / filename).read_bytes()).hexdigest()}

    def make_run(self, key, upstream=None, status='complete'):
        record = {'run_id': 'run-' + key, 'stage_id': key, 'case_id': 'case-' + key, 'status': status, 'upstream_run_ids': upstream or []}
        if status == 'complete':
            record.update(provenance={'config': self.claim('config.json'), 'code': self.claim('code.py'), 'input_fingerprint': 'input-revision-1'}, checks={'tolerance': 'passed'}, artifacts=[dict(output_id='result', **self.claim('result.dat'))])
        return record

    def invoke(self, records=None, changed=(), raw_spec=None, raw_runs=None, extra=()):
        spec_path = self.base / 'spec.json'
        spec_path.write_text(raw_spec if raw_spec is not None else json.dumps(self.spec), encoding='utf-8')
        command = [sys.executable, str(SCRIPT), '--spec', str(spec_path)]
        if records is not None or raw_runs is not None:
            runs_path = self.base / 'runs.json'
            runs_path.write_text(raw_runs if raw_runs is not None else json.dumps({'schema_version': 1, 'workflow_id': 'example', 'runs': records}), encoding='utf-8')
            command += ['--runs', str(runs_path), '--root', str(self.root)]
        for stage_id in changed:
            command += ['--changed-stage', stage_id]
        command += list(extra)
        process = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(process.stderr, '')
        return process.returncode, json.loads(process.stdout)

    def codes(self, report):
        return {error['code'] for error in report['errors']}

    def test_valid_design(self):
        code, report = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(report['audit_mode'], 'design_only')
        self.assertEqual(report['completion'], 'not_evaluated')
        self.assertEqual(report['scope']['scientific_validation'], 'not_evaluated')

    def test_cycle(self):
        self.spec['stages'][0]['depends_on'] = ['c']
        code, report = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertIn('cycle', self.codes(report))

    def test_transitive_impact(self):
        code, report = self.invoke(changed=['a'])
        self.assertEqual(code, 0)
        self.assertEqual(report['affected_stages'], ['a', 'b', 'c'])
        self.assertIn('candidate_invalidation_only', report['impact_action'])

    def test_complete_cross_case_runs_with_real_evidence(self):
        # Records are deliberately unordered and case IDs differ.
        records = [self.make_run('b', ['run-a']), self.make_run('a')]
        before = {file.name: file.read_bytes() for file in self.root.iterdir()}
        code, report = self.invoke(records=records)
        self.assertEqual(code, 0, report)
        self.assertEqual(report['completion'], 'records_complete')
        self.assertEqual(report['scope']['kind'], 'provided_run_records')
        self.assertEqual(report['scope']['matrix_coverage'], 'not_evaluated')
        self.assertEqual(report['scope']['record_count'], 2)
        self.assertEqual(before, {file.name: file.read_bytes() for file in self.root.iterdir()})

    def test_tampered_output(self):
        record = self.make_run('a')
        (self.root / 'result.dat').write_text('changed', encoding='utf-8')
        code, report = self.invoke(records=[record])
        self.assertNotEqual(code, 0)
        self.assertIn('hash_mismatch', self.codes(report))
        self.assertEqual(report['completion'], 'incomplete')

    def test_tampered_upstream_propagates_failure(self):
        parent = self.make_run('a')
        downstream = self.make_run('b', ['run-a'])
        parent['provenance']['config']['sha256'] = '0' * 64
        code, report = self.invoke(records=[downstream, parent])
        self.assertNotEqual(code, 0)
        self.assertIn('hash_mismatch', self.codes(report))
        self.assertIn('upstream_audit_failed', self.codes(report))
        self.assertTrue(all(record['audit'] == 'failed' for record in report['records']))

    def test_missing_upstream(self):
        code, report = self.invoke(records=[self.make_run('b')])
        self.assertNotEqual(code, 0)
        self.assertIn('missing_upstream', self.codes(report))

    def test_unknown_upstream(self):
        code, report = self.invoke(records=[self.make_run('b', ['missing'])])
        self.assertNotEqual(code, 0)
        self.assertIn('unknown_upstream', self.codes(report))

    def test_noncomplete_upstream(self):
        code, report = self.invoke(records=[self.make_run('a', status='partial'), self.make_run('b', ['run-a'])])
        self.assertNotEqual(code, 0)
        self.assertIn('upstream_incomplete', self.codes(report))

    def test_partial_is_not_success(self):
        code, report = self.invoke(records=[self.make_run('a', status='partial')])
        self.assertNotEqual(code, 0)
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['completion'], 'incomplete')
        self.assertEqual(report['records'][0]['audit'], 'incomplete')

    def test_all_noncomplete_states_are_not_success(self):
        for status in ('planned', 'running', 'failed', 'stale'):
            with self.subTest(status=status):
                code, report = self.invoke(records=[self.make_run('a', status=status)])
                self.assertNotEqual(code, 0)
                self.assertEqual(report['completion'], 'incomplete')

    def test_planned_null_evidence_is_unexecuted(self):
        record = self.make_run('a', status='planned')
        record['provenance'] = {'config': {'path': None, 'sha256': None}, 'code': {'path': None, 'sha256': None}, 'input_fingerprint': None}
        record['checks'] = {'tolerance': 'pending'}
        record['artifacts'] = []
        code, report = self.invoke(records=[record])
        self.assertEqual(code, 1)
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['completion'], 'incomplete')

    def test_partial_nonnull_path_cannot_escape_with_missing_hash(self):
        record = self.make_run('a', status='partial')
        record['provenance'] = {'code': {'path': '../outside.py', 'sha256': None}}
        code, report = self.invoke(records=[record])
        self.assertEqual(code, 1)
        self.assertIn('path_escape', self.codes(report))

    def test_duplicate_stage(self):
        self.spec['stages'].append(copy.deepcopy(self.spec['stages'][0]))
        code, report = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertIn('duplicate_stage', self.codes(report))

    def test_duplicate_run(self):
        record = self.make_run('a')
        code, report = self.invoke(records=[record, record])
        self.assertNotEqual(code, 0)
        self.assertIn('duplicate_run', self.codes(report))

    def test_duplicate_criterion(self):
        self.spec['stages'][0]['acceptance'] *= 2
        code, report = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertIn('duplicate_id', self.codes(report))

    def test_duplicate_check_json_key(self):
        runs = json.dumps({'schema_version': 1, 'workflow_id': 'example', 'runs': [self.make_run('a')]})
        runs = runs.replace('"tolerance": "passed"', '"tolerance": "passed", "tolerance": "failed"')
        code, report = self.invoke(raw_runs=runs)
        self.assertNotEqual(code, 0)
        self.assertIn('input_error', self.codes(report))

    def test_path_escape_absolute_and_relative(self):
        outside = self.base / 'outside.txt'
        outside.write_text('outside', encoding='utf-8')
        digest = hashlib.sha256(outside.read_bytes()).hexdigest()
        for path in ('../outside.txt', str(outside)):
            with self.subTest(path=path):
                record = self.make_run('a')
                record['artifacts'][0].update(path=path, sha256=digest)
                code, report = self.invoke(records=[record])
                self.assertNotEqual(code, 0)
                self.assertIn('path_escape', self.codes(report))

    def test_symlink_escape(self):
        outside = self.base / 'outside.txt'
        outside.write_text('outside', encoding='utf-8')
        link = self.root / 'link.txt'
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest('Symlinks unavailable on this platform')
        record = self.make_run('a')
        record['provenance']['code'] = self.claim('link.txt')
        code, report = self.invoke(records=[record])
        self.assertNotEqual(code, 0)
        self.assertIn('path_escape', self.codes(report))

    def test_failed_or_pending_acceptance(self):
        for state in ('failed', 'pending', None):
            with self.subTest(state=state):
                record = self.make_run('a')
                record['checks'] = {} if state is None else {'tolerance': state}
                code, report = self.invoke(records=[record])
                self.assertNotEqual(code, 0)
                self.assertIn('acceptance_not_passed', self.codes(report))

    def test_unknown_stage_dependency_output_and_check(self):
        self.spec['stages'][0]['depends_on'] = ['absent']
        code, report = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertIn('unknown_dependency', self.codes(report))
        self.spec['stages'][0]['depends_on'] = []
        record = self.make_run('a')
        record['checks']['unknown'] = 'passed'
        record['artifacts'][0]['output_id'] = 'unknown'
        code, report = self.invoke(records=[record])
        self.assertNotEqual(code, 0)
        self.assertTrue({'unknown_output', 'unknown_check'} <= self.codes(report))
        record['stage_id'] = 'absent'
        code, report = self.invoke(records=[record])
        self.assertNotEqual(code, 0)
        self.assertIn('unknown_stage', self.codes(report))

    def test_missing_provenance_or_output(self):
        for key in ('provenance', 'artifacts'):
            with self.subTest(key=key):
                record = self.make_run('a')
                del record[key]
                code, report = self.invoke(records=[record])
                self.assertNotEqual(code, 0)
                self.assertEqual(report['completion'], 'incomplete')

    def test_directory_is_not_code_snapshot(self):
        record = self.make_run('a')
        record['provenance']['code'] = {'path': '.', 'sha256': '0' * 64}
        code, report = self.invoke(records=[record])
        self.assertNotEqual(code, 0)
        self.assertIn('missing_file', self.codes(report))

    def test_malformed_json_and_cli_emit_json(self):
        code, report = self.invoke(raw_spec='{')
        self.assertNotEqual(code, 0)
        self.assertIn('input_error', self.codes(report))
        code, report = self.invoke(extra=['--unknown'])
        self.assertNotEqual(code, 0)
        self.assertIn('input_error', self.codes(report))


if __name__ == '__main__':
    unittest.main()
