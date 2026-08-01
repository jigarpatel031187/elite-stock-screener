import json, pathlib, unittest
from importlib import import_module

scripts_path = pathlib.Path('scripts')
import sys
if str(scripts_path) not in sys.path:
    sys.path.insert(0, str(scripts_path))

digest = import_module('digest')

class DigestTest(unittest.TestCase):
    def test_build_digest_returns_md_and_state(self):
        qpath = pathlib.Path('docs/data/queue_latest.json')
        q = json.loads(qpath.read_text())
        trade_date = q.get('trade_date', '')
        md, state = digest.build_digest(q, trade_date)
        # basic sanity checks
        self.assertIsInstance(md, str)
        self.assertIn(trade_date, md)
        self.assertIsInstance(state, dict)
        self.assertIn('lane1', state)
        self.assertIn('lane2', state)
        # state lanes should be lists of symbols
        self.assertTrue(all(isinstance(s, str) for s in state['lane1']))
        self.assertTrue(all(isinstance(s, str) for s in state['lane2']))

if __name__ == '__main__':
    unittest.main()
