import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import database
import platform_family_intelligence as intelligence
from platform_family_intelligence import (CompositeSearchProvider, FamilyIntelligenceEngine,
    IdentityBudgetExceeded, Page, SearchHit, SearchUnavailable)


class BudgetTests(unittest.TestCase):
    def test_fallback_observes_shared_deadline(self):
        clock=[0.0]
        primary=Mock(); backup=Mock()
        def timeout(*args):
            clock[0]=4
            raise intelligence.requests.Timeout()
        primary.search.side_effect=timeout
        backup.search.return_value=[SearchHit('https://example.org')]
        with patch.object(intelligence.time, 'monotonic', side_effect=lambda:clock[0]):
            rows=CompositeSearchProvider([primary,backup]).search('example',6,deadline=5)
        self.assertEqual(len(rows),1)
        backup.search.assert_called_once_with('example',1)

    def test_expired_deadline_skips_network_and_all_failures_are_not_no_data(self):
        provider=Mock(); provider.search.side_effect=intelligence.requests.Timeout()
        with self.assertRaises(IdentityBudgetExceeded):
            CompositeSearchProvider([provider]).search('example',6,deadline=0)
        provider.search.assert_not_called()
        with self.assertRaises(SearchUnavailable):
            CompositeSearchProvider([provider]).search('example',6)

    def test_incomplete_investigation_is_explicit_and_has_short_retry(self):
        clock=[0.0]
        search=Mock(); pages=Mock(); pages.fetch.return_value=Page('')
        def slow_query(*args):
            clock[0]+=11
            return []
        search.search.side_effect=slow_query
        engine=FamilyIntelligenceEngine(search=search,pages=pages,total_timeout=10)
        with patch.object(intelligence.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(engine,'_direct_address_identity',return_value=([],[])):
            result=engine.enrich({'id':1,'creator':'0x'+'1'*40,'brand_hint':'Known name'})
        self.assertEqual(result['verification_status'],'NO_DATA')
        self.assertEqual(result['execution_status'],'BUDGET_EXHAUSTED')
        self.assertTrue(any(e.get('status')=='SKIPPED' and 'Known name' in e.get('query','') for e in result['evidence']))
        with tempfile.TemporaryDirectory() as tmp, patch.object(database,'DB_PATH',Path(tmp)/'db.sqlite'):
            database.init_platform_family_intelligence_db()
            for _ in range(12):
                database.save_platform_identity_investigation({'subject_key':'family:1','verification_status':'NO_DATA'})
            retried=database.save_platform_identity_investigation(result)
            self.assertEqual(retried['attempt_count'],13)
            self.assertEqual(retried['backoff_seconds'],300)


if __name__=='__main__':
    unittest.main()
