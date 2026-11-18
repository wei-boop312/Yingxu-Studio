from pathlib import Path
import unittest
from workflow_architect import __version__
from workflow_architect.settings import GENERATOR_VERSION

class FrontendVersionTests(unittest.TestCase):
    def test_visible_versions_and_exports_match_backend_without_changing_extension_id(self):
        source=(Path(__file__).parents[1]/'web/js/workflow_architect.js').read_text(encoding='utf-8')
        self.assertEqual(__version__,GENERATOR_VERSION)
        self.assertIn('"aria-label": "打开 AI 工作流架构师 '+__version__+'"',source)
        self.assertIn('text: "AI 工作流架构师 '+__version__+'"',source)
        self.assertIn('-v'+__version__+'.json`',source)
        self.assertIn('V'+__version__+' · ',source)
        self.assertIn('name: "workflow.architect.v2.12.0"',source)
