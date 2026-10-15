"""Run focused offline regressions with loopback-only connection auditing."""
import os
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.ui_fixture import audit

MODULES=('analysis_validation_repair','analysis_failures','finalize','validate','roles',
         'replay','entry_quality','cli_stages','tasks','models','runtime_launcher',
         'demo_launcher','ui_backend','ui_integration','offline_pipeline','auth',
         'evidence_text','web','net','url_guard','feedback','web_feedback','unified_execution')

def main():
    for key in list(os.environ):
        if key.startswith(('LEARNRADAR_','GITHUB_')) and key!='LEARNRADAR_PYTHON':os.environ.pop(key)
    os.environ['NO_PROXY']='localhost,127.0.0.1,::1'
    sys.addaudithook(audit)
    suite=unittest.defaultTestLoader.loadTestsFromNames(['tests.test_'+name for name in MODULES])
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    print('fixture offline regressions; real business external requests: 0')
    return 0 if result.wasSuccessful() else 1

if __name__=='__main__':raise SystemExit(main())
