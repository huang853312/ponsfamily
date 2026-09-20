import unittest
from unittest.mock import patch
import monitor as m


class FlowTests(unittest.TestCase):
    def test_direction_and_exclude_unfinished_day(self):
        data = [{'date': 86400, 'depositUSD': 90, 'withdrawUSD': 30},
                {'date': 172800, 'depositUSD': 99999, 'withdrawUSD': 1}]
        got = m.daily_flow(data, 172801, 'inflow')
        self.assertEqual(got['net_usd'], 60)
        self.assertEqual(got['day'], 86400)
        self.assertEqual(m.daily_flow(data, 172801, 'outflow')['net_usd'], -60)
        self.assertEqual(m.daily_flow(data, 172801)['status'], 'direction_unverified')

    def test_missing_day_is_not_zero(self):
        got = m.daily_flow([{'date': 0, 'depositUSD': 5, 'withdrawUSD': 1}], 172801)
        self.assertEqual(got['status'], 'missing_completed_day')
        self.assertNotIn('net_usd', got)

    def test_invalid_numbers_rejected(self):
        for value in (None, float('nan'), float('inf'), -1, True, 'unknown'):
            with self.subTest(value=value), self.assertRaises(m.DataError):
                m.number(value)

    def test_duplicate_day_rejected(self):
        with self.assertRaises(m.DataError):
            m.daily_flow([{'date': 86400}, {'date': 86400}], 172801)

    def test_no_baseline_and_missing_metrics(self):
        current = {'collected_at': 5000, 'chains': {'Base': {'tvl': 100}}}
        m.add_changes(current, None)
        self.assertNotIn('tvl_change', current['chains']['Base'])
        m.add_changes(current, {'collected_at': 1400, 'chains': {'Base': {'stable_usd': 200}}})
        self.assertNotIn('tvl_change', current['chains']['Base'])

    def test_long_outage_not_reported_as_hourly(self):
        current = {'collected_at': 200001, 'chains': {'Base': {'tvl': 100}}}
        m.add_changes(current, {'collected_at': 1, 'chains': {'Base': {'tvl': 10}}})
        self.assertNotIn('tvl_change', current['chains']['Base'])

    def test_bridge_catalog_and_destination(self):
        got = m.bridge_chains({'bridges': [{'chains': ['Ethereum'], 'destinationChain': 'Base'},
                                          {'chains': ['Solana'], 'destinationChain': 'false'}]})
        self.assertEqual(got, ['Base', 'Ethereum', 'Solana'])

    def test_one_missing_chain_does_not_hide_good_chains(self):
        values, errors = m.named([{'name': 'Base', 'totalCirculatingUSD': {'peggedUSD': 123}},
                                  {'name': 'NoUSD', 'totalCirculatingUSD': {}}], 'stable_usd')
        self.assertEqual(values, {'Base': 123})
        self.assertIn('NoUSD', errors)

    def test_failure_not_presented_as_zero(self):
        with patch.dict('os.environ', {'DEFILLAMA_API_KEY': ''}), patch.object(m, 'get_json', side_effect=m.DataError('HTTP_429')):
            data = m.collect(172801)
        self.assertEqual(data['chains'], {})
        self.assertIn('暂无有效数据', m.render(data))
        self.assertNotIn('净流入排行', m.render(data))

    def test_telegram_failure_does_not_mark_sent(self):
        db = m.open_db(':memory:')
        with patch.dict('os.environ', {'FLOW_TELEGRAM_BOT_TOKEN': 'test', 'FLOW_TELEGRAM_CHAT_ID': '123'}), patch.object(m, 'get_json', return_value={'ok': False}):
            with self.assertRaises(m.DataError):
                m.send_report('测试', db, 1)
        self.assertEqual(db.execute('SELECT count(*) FROM sent').fetchone()[0], 0)
        db.close()

    def test_old_bot_environment_is_never_used(self):
        db = m.open_db(':memory:')
        with patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': 'old-test-token', 'TELEGRAM_CHAT_ID': 'old-chat'}, clear=True), patch.object(m, 'get_json') as request:
            with self.assertRaises(m.DataError):
                m.send_report('测试', db, 1)
            request.assert_not_called()
        db.close()


if __name__ == '__main__':
    unittest.main()
