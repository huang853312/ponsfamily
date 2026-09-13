import unittest
from unittest.mock import Mock
from platform_family_intelligence import CompositeSearchProvider, SearchHit
class QualityTests(unittest.TestCase):
    def test_unrelated_primary_uses_backup(self):
        address='0x'+'a'*40
        primary=Mock(); primary.search.return_value=[SearchHit('https://docs.fivem.net/','Game docs')]
        backup=Mock(); backup.search.return_value=[SearchHit('https://project.xyz/docs',snippet=address.upper())]
        result=CompositeSearchProvider([primary,backup]).search('"'+address+'"',1)
        self.assertEqual(result,backup.search.return_value); backup.search.assert_called_once()
    def test_unrelated_all_is_no_data(self):
        p=Mock(); p.search.return_value=[SearchHit('https://githubstatus.com/')]
        self.assertEqual(CompositeSearchProvider([p]).search('0x'+'a'*40,1),[])
if __name__=='__main__': unittest.main()
