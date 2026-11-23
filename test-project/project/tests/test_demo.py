from pathlib import Path
import unittest


class DemoSmokeTests(unittest.TestCase):
    def test_offline_app_has_no_external_dependencies(self):
        page = (Path(__file__).resolve().parents[1] / 'src' / 'index.html').read_text()
        self.assertIn('localStorage', page)
        self.assertIn('JSON.stringify', page)
        self.assertNotIn('src="http', page)
        self.assertNotIn('href="http', page)
