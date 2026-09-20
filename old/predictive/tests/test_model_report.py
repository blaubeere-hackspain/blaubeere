import copy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest

from scripts import render_model_report as report


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.current = None
        self.resources = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.current = [attrs, '']
            self.scripts.append(self.current)
        if 'src' in attrs or tag == 'link':
            self.resources.append(attrs)

    def handle_endtag(self, tag):
        if tag == 'script':
            self.current = None

    def handle_data(self, data):
        if self.current is not None:
            self.current[1] += data


class ModelReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = report.load_report(company_ids=['COMP_0001', 'COMP_0003'])

    def test_export_preserves_unknowns_and_excluded_companies(self):
        self.assertEqual([c['id'] for c in self.data['companies']], ['COMP_0001', 'COMP_0003'])
        for company in self.data['companies']:
            self.assertEqual(len(company['points']), 24)
            self.assertNotIn('snapshot_context', company)
            for point in company['points']:
                self.assertIsNone(point['score']['headline'])
                self.assertNotIn('features', point)
        excluded = next(c for c in self.data['companies'] if c['id'] == 'COMP_0003')
        self.assertEqual(excluded['model_points'], [])
        self.assertEqual(excluded['model_reason'], 'final_test_product_only_no_model_inference')
        self.assertFalse(self.data['automatic_alerts'])
        for target in self.data['metrics'].values():
            self.assertFalse(target['utility']['pass'])

    def test_default_selection_and_conflicting_options(self):
        data = report.load_report()
        self.assertEqual([c['id'] for c in data['companies']], ['COMP_0001', 'COMP_0002', 'COMP_0004'])
        self.assertEqual(data['selection'], 'first_three_non_holdout_ids')
        with self.assertRaises(ValueError):
            report.load_report(company_ids=['COMP_0001'], all_companies=True)
        with self.assertRaises(ValueError):
            report.load_report(company_ids=[])

    def test_all_companies_keep_every_closed_month_and_model_abstention(self):
        data = report.load_report(all_companies=True)
        self.assertEqual(len(data['companies']), 1286)
        self.assertEqual(sum(len(c['points']) for c in data['companies']), 30864)
        self.assertEqual(sum(c['model_reason'] is not None for c in data['companies']), 208)
        self.assertEqual(data['selection'], 'all')

    def test_output_cannot_enter_scientific_directories(self):
        for name in ['reports/modeling/new-report.html', 'data/new-report.html', 'scripts/new-report.html']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                report.save_report(report.ROOT / name, 'test')

    def test_invalid_json_fails_closed(self):
        for content in ['{"x":NaN}', '{"x":Infinity}', '{"x":1e1000}', '{"x":1,"x":2}']:
            with self.subTest(content=content), self.assertRaises(ValueError):
                report.parse_json(content)

    def test_unknown_company_fails_instead_of_substitution(self):
        for company in ['COMP_9999', '../index', '']:
            with self.subTest(company=company), self.assertRaises(ValueError):
                report.load_report(company_ids=[company])

    def test_pinned_local_reader_rejects_tampering_and_path_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = b'{"value":null}'
            (root / 'input.json').write_bytes(content)
            pin = hashlib.sha256(content).hexdigest()
            self.assertEqual(report.read_verified(root, 'input.json', pin), {'value': None})
            for path in ['../input.json', '/input.json', 'https://example/input.json', 'x\\input.json']:
                with self.subTest(path=path), self.assertRaises(ValueError):
                    report.read_verified(root, path, pin)
            (root / 'link.json').symlink_to(root / 'input.json')
            with self.assertRaises(ValueError):
                report.read_verified(root, 'link.json', pin)
            (root / 'input.json').write_bytes(content + b' ')
            with self.assertRaises(ValueError):
                report.read_verified(root, 'input.json', pin)

    def test_html_is_self_contained_and_script_safe(self):
        data = copy.deepcopy(self.data)
        data['companies'][0]['name'] = '</script><script>alert(1)</script>&<img src=x>'
        html = report.render_report(data)
        parsed = Scripts()
        parsed.feed(html)
        self.assertEqual(len(parsed.scripts), 2)
        self.assertEqual(parsed.resources, [])
        self.assertNotIn('fetch(', html)
        self.assertIn("connect-src 'none'", html)
        payload = next(text for attrs, text in parsed.scripts if attrs.get('id') == 'report-data')
        self.assertEqual(json.loads(payload), data)
        self.assertNotIn('</script><script>alert', html)
        self.assertIn('No disponible', html)
        self.assertIn('no es salud financiera', html)

    def test_deterministic_output_check_and_no_overwrite(self):
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (
            report.DATASET / 'manifest.json', report.DATASET / 'model-v1/manifest.json')}
        html = report.render_report(self.data)
        self.assertEqual(report.render_report(self.data), html)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'report.html'
            with self.assertRaises(FileNotFoundError):
                report.save_report(output, html, check=True)
            self.assertFalse(output.exists())
            report.save_report(output, html)
            report.save_report(output, html, check=True)
            report.save_report(output, html)
            with self.assertRaises(ValueError):
                report.save_report(output, html + 'changed')
            self.assertEqual(output.read_text(), html)
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before})


if __name__ == '__main__':
    unittest.main()
