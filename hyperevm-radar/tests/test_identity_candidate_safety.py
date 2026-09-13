import unittest
from unittest.mock import patch
import platform_family_intelligence as m
A='0x'+'a'*40
class Search:
    def __init__(self,hits): self.hits=hits
    def search(self,q,t): return self.hits
class Pages:
    def __init__(self,pages): self.pages=pages
    def fetch(self,u,t): return self.pages.get(u,m.Page(u))
class Engine(m.FamilyIntelligenceEngine):
    def _direct_address_identity(self,*args): return [],[]
class Response:
    def raise_for_status(self): pass
    def json(self): return {'pairs':[]}
class SafetyTests(unittest.TestCase):
    def run_case(self,pages,hits,x=None):
        with patch.object(m.requests,'get',return_value=Response()):
            return Engine(search=Search([m.SearchHit(u) for u in hits]),pages=Pages(pages),x_provider=Pages(x or {}),total_timeout=500).enrich({'id':1,'creator':A})
    def test_status_backlink_cannot_displace_address_website(self):
        good='https://realproject.xyz/'; repo='https://github.com/example/project'; status='https://www.githubstatus.com/'
        pages={repo:m.Page(repo,links=[status],status='AVAILABLE'),status:m.Page(status,title='GitHub Status',links=['https://x.com/github'],status='AVAILABLE'),good:m.Page(good,text=A,title='Real Project',status='AVAILABLE')}
        r=self.run_case(pages,[good,repo]); self.assertEqual(r['official_website'],good); self.assertEqual(r['verification_status'],'VERIFIED')
    def test_different_docs_cannot_combine_evidence(self):
        good='https://realproject.xyz/'; alien='https://alien.gitbook.io/docs/contracts'; trusted='https://realproject.xyz/docs'
        pages={good:m.Page(good,title='Real Project',links=[trusted],status='AVAILABLE'),alien:m.Page(alien,text=A,status='AVAILABLE'),trusted:m.Page(trusted,text='no target address',status='AVAILABLE')}
        r=self.run_case(pages,[good,alien,trusted]); self.assertNotEqual(r['verification_status'],'VERIFIED')
    def test_same_linked_doc_address_verifies(self):
        good='https://realproject.xyz/'; doc='https://realproject.xyz/docs'
        pages={good:m.Page(good,title='Real Project',links=[doc],status='AVAILABLE'),doc:m.Page(doc,text=A,status='AVAILABLE')}
        self.assertEqual(self.run_case(pages,[good,doc])['verification_status'],'VERIFIED')
    def test_social_link_alone_is_not_website_relevance(self):
        u='https://unrelated.xyz/'
        r=self.run_case({u:m.Page(u,title='Unrelated',links=['https://x.com/unrelated'],status='AVAILABLE')},[u])
        self.assertEqual(r['official_website'],''); self.assertNotEqual(r['verification_status'],'VERIFIED')
if __name__=='__main__': unittest.main()
