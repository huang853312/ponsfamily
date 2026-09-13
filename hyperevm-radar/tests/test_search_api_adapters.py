import os
import unittest
from unittest.mock import Mock, patch
import requests
from platform_family_intelligence import ConfiguredJsonSearchProvider, CompositeSearchProvider, YouSearchProvider, ExaSearchProvider, TinyFishSearchProvider

class SearchAdapterTests(unittest.TestCase):
    def response(self,payload):
        response=Mock();response.json.return_value=payload;return response
    def test_langsearch_posts_raw_query_and_parses_results(self):
        p=self.response({"data":{"webPages":{"value":[{"url":"https://hyperlend.finance","name":"HyperLend","snippet":"Lending"}]}}})
        with patch("platform_family_intelligence.requests.post",return_value=p) as post, patch("platform_family_intelligence.requests.get") as get:
            hits=ConfiguredJsonSearchProvider("https://api.langsearch.com/v1/web-search","test").search('"0x123" HyperEVM',6)
        get.assert_not_called()
        self.assertEqual(post.call_args.kwargs["json"]["query"],'"0x123" HyperEVM')
        self.assertEqual(hits[0].title,"HyperLend")
        self.assertEqual(post.call_args.kwargs["timeout"],6)
    def test_other_json_endpoints_keep_get(self):
        with patch("platform_family_intelligence.requests.get",return_value=self.response({"results":[{"url":"https://example.com"}]})) as get:
            hits=ConfiguredJsonSearchProvider("https://example.com/?q={query}","test").search("a b",6)
        self.assertEqual(get.call_args.args[0],"https://example.com/?q=a+b");self.assertEqual(len(hits),1)
    def test_all_keyed_providers_authenticate_and_parse(self):
        fixtures=[(YouSearchProvider,"YDC_API_KEY","post","X-API-Key",{"results":{"web":[{"url":"https://example.com","snippets":["hello"]}]}}),
            (ExaSearchProvider,"EXA_API_KEY","post","x-api-key",{"results":[{"url":"https://example.com"}]}),
            (TinyFishSearchProvider,"TINYFISH_API_KEY","get","X-API-Key",{"results":[{"url":"https://example.com"}]})]
        for cls,key,method,header,payload in fixtures:
            with self.subTest(provider=cls.__name__),patch.dict(os.environ,{key:"test"}),patch("platform_family_intelligence.requests."+method,return_value=self.response(payload)) as call:
                self.assertEqual(len(cls().search("query",6)),1)
                self.assertEqual(call.call_args.kwargs["headers"][header],"test")
    def test_default_chain_contains_all_four_apis(self):
        providers=CompositeSearchProvider().providers
        self.assertEqual([type(p).__name__ for p in providers[:4]],["ConfiguredJsonSearchProvider","YouSearchProvider","ExaSearchProvider","TinyFishSearchProvider"])
    def test_http_failure_falls_back_to_next_api(self):
        first=Mock();first.search.side_effect=requests.HTTPError("405")
        second=Mock();second.search.return_value=ConfiguredJsonSearchProvider._hits([{"url":"https://example.com"}],"fallback")
        last=Mock()
        self.assertEqual(len(CompositeSearchProvider([first,second,last]).search("query",6)),1)
        second.search.assert_called_once();last.search.assert_not_called()
    def test_unconfigured_providers_do_not_request(self):
        with patch.dict(os.environ,{},clear=True),patch("platform_family_intelligence.requests.get") as get,patch("platform_family_intelligence.requests.post") as post:
            for cls in [YouSearchProvider,ExaSearchProvider,TinyFishSearchProvider]:self.assertEqual(cls().search("query",6),[])
        get.assert_not_called();post.assert_not_called()
if __name__=="__main__": unittest.main()
