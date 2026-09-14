"""Run the unchanged tests offline and report each failure, including old ones."""
import argparse
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import requests

parser = argparse.ArgumentParser()
parser.add_argument('--source', required=True)
parser.add_argument('--baseline', action='store_true')
args = parser.parse_args()
source = Path(args.source).resolve()
sys.path.insert(0, str(source))
suite = unittest.TestSuite()
new_tests = {'test_execution_recovery.py', 'test_identity_execution_budget.py'}
for file in sorted((source / 'tests').glob('test_*.py')):
    if args.baseline and file.name in new_tests:
        continue
    suite.addTests(unittest.defaultTestLoader.discover(str(source / 'tests'), pattern=file.name))
with patch('requests.get', side_effect=requests.ConnectionError('UNIT_TEST_NETWORK_DISABLED')), \
     patch('requests.post', side_effect=requests.ConnectionError('UNIT_TEST_NETWORK_DISABLED')):
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
print('TEST_REPORT', json.dumps({'count': result.testsRun,
    'failures': [test.id() for test, _ in result.failures],
    'errors': [test.id() for test, _ in result.errors],
    'details': [{'test': test.id(), 'traceback': trace} for test, trace in result.failures + result.errors]}), flush=True)
