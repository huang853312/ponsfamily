import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

import database
import execution_state as state
import main
import notifier
from detectors.token import detect_erc20, TokenMetadataUnavailable
from monitors import blocks
from web3.exceptions import ContractLogicError, Web3RPCError

ADDRESS = '0x' + '1' * 40
CREATOR = '0x' + '2' * 40
EVENT = dict(address=ADDRESS, creator=CREATOR, block_number=100, tx_hash='audit')


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        path = Path(self.temp) / 'radar.db'
        for module in (database, main, blocks):
            self.stack.enter_context(patch.object(module, 'DB_PATH', path))
        for init in (database.init_db, database.init_cluster_db, database.init_relation_db,
                     database.init_platform_brand_db, database.init_platform_token_match_db,
                     database.init_platform_candidate_db, database.init_platform_family_db,
                     database.init_platform_family_intelligence_db, state.init_execution_db):
            init()

    def rows(self, query):
        with state.connection() as conn:
            return [dict(r) for r in conn.execute(query)]

    def contract_mocks(self):
        for name, value in {
            'fingerprint': {'code_hash': 'a', 'template_key': 'a', 'implementation': '',
                            'code_size': 1, 'is_eip1167': False, 'eip1967_hint': False},
            'extract_words': set(),
            'classify': {'level': 'A_CANDIDATE', 'stock_hits': [], 'infra_hits': ['vault']},
            'classify_platform_words': {'eligible': False},
            'load_deployers': {}, 'save_deployer': None,
        }.items():
            self.stack.enter_context(patch.object(main, name, return_value=value))
        self.stack.enter_context(patch.object(main, 'detect_erc20', AsyncMock(return_value=None)))

    async def test_saved_contract_failure_replays_then_retries_delivery_after_restart(self):
        self.contract_mocks()
        monitor = blocks.HyperEVMBlockMonitor()
        monitor.get_code = AsyncMock(return_value=b'x')
        monitor.get_contract_creations = AsyncMock(return_value=[EVENT])
        monitor.scan_position(100)
        with patch.object(main, 'enqueue_notification', side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError):
                await monitor.scan_block(100, main.handle_contract)
        self.assertTrue(database.seen(ADDRESS))
        self.assertEqual(monitor.scan_position(999), 100)
        self.assertEqual(self.rows('SELECT state FROM contract_processing')[0]['state'], 'pending')
        # Fresh monitor and database connections, using the persisted pending marker.
        replay = blocks.HyperEVMBlockMonitor()
        replay.get_code = AsyncMock(return_value=b'x')
        replay.get_contract_creations = AsyncMock(return_value=[EVENT])
        await replay.scan_block(replay.scan_position(999), main.handle_contract)
        self.assertEqual(replay.scan_position(999), 101)
        self.assertEqual(len(self.rows('SELECT * FROM telegram_outbox')), 1)
        sender = AsyncMock(side_effect=RuntimeError('simulated Telegram outage'))
        await state.deliver_pending_once(sender, now=1000)
        self.assertEqual(self.rows('SELECT status FROM telegram_outbox')[0]['status'], 'pending')
        sender = AsyncMock(return_value=True)
        await state.deliver_pending_once(sender, now=1006)
        await main.handle_contract(replay, EVENT)
        await state.deliver_pending_once(sender, now=2000)
        sender.assert_awaited_once()
        self.assertEqual(self.rows('SELECT status,attempts FROM telegram_outbox')[0], {'status': 'sent', 'attempts': 2})

    async def test_interrupted_send_is_reclaimed_after_lease_not_before(self):
        state.enqueue_notification('crash-test', 'test')
        claim = state.claim_notification(now=1000)
        self.assertIsNotNone(claim)
        sender = AsyncMock(return_value=True)
        self.assertFalse(await state.deliver_pending_once(sender, now=1100))
        self.assertTrue(await state.deliver_pending_once(sender, now=1121))
        sender.assert_awaited_once()

    async def test_metadata_rpc_failure_does_not_record_non_token_or_advance(self):
        self.contract_mocks()
        monitor = blocks.HyperEVMBlockMonitor()
        monitor.get_code = AsyncMock(return_value=b'x')
        monitor.get_contract_creations = AsyncMock(return_value=[EVENT])
        monitor.w3.eth.call = AsyncMock(side_effect=RuntimeError('-32005 rate limited'))
        monitor.scan_position(100)
        with patch.object(main, 'detect_erc20', detect_erc20):
            with self.assertRaises(TokenMetadataUnavailable):
                await monitor.scan_block(100, main.handle_contract)
        self.assertFalse(database.seen(ADDRESS))
        self.assertEqual(monitor.scan_position(999), 100)

    async def test_optional_metadata_failure_is_retriable_but_revert_is_not(self):
        w3 = blocks.HyperEVMBlockMonitor().w3
        number = lambda value: value.to_bytes(32, 'big')
        w3.eth.call = AsyncMock(side_effect=[number(100), number(18), RuntimeError('rate limited')])
        with self.assertRaises(TokenMetadataUnavailable):
            await detect_erc20(w3, ADDRESS)
        w3.eth.call = AsyncMock(side_effect=ContractLogicError('execution reverted'))
        self.assertIsNone(await detect_erc20(w3, ADDRESS))
        w3.eth.call = AsyncMock(side_effect=[number(100), number(18), ContractLogicError('execution reverted'), b'Name'.ljust(32,b'\0')])
        self.assertEqual((await detect_erc20(w3, ADDRESS))['name'], 'Name')

    async def test_captured_invalid_opcode_does_not_stall_block_but_rate_limit_does(self):
        self.contract_mocks()
        monitor=blocks.HyperEVMBlockMonitor()
        monitor.get_code=AsyncMock(return_value=b'x')
        monitor.get_contract_creations=AsyncMock(return_value=[EVENT])
        monitor.scan_position(100)
        monitor.w3.eth.call=AsyncMock(side_effect=Web3RPCError("{'code': -32003, 'message': 'EVM error: InvalidFEOpcode'}"))
        with patch.object(main,'detect_erc20',detect_erc20):
            await monitor.scan_block(100,main.handle_contract)
        self.assertEqual(monitor.scan_position(999),101)
        self.assertTrue(database.seen(ADDRESS))
        for error in ["{'code': -32005, 'message': 'rate limited'}", "{'code': -32003, 'message': 'node unavailable'}"]:
            monitor.w3.eth.call=AsyncMock(side_effect=Web3RPCError(error))
            with self.assertRaises(TokenMetadataUnavailable):
                await detect_erc20(monitor.w3,ADDRESS)

    async def test_invalid_opcode_pool_stays_unverified_and_rpc_failure_propagates(self):
        monitor=blocks.HyperEVMBlockMonitor()
        info=dict(pool=ADDRESS,factory=CREATOR,token0='0x'+'3'*40,token1='0x'+'4'*40,type='V2_PAIR')
        monitor.w3.eth.get_code=AsyncMock(return_value=b'x')
        monitor.w3.eth.call=AsyncMock(side_effect=Web3RPCError("{'code': -32003, 'message': 'EVM error: InvalidFEOpcode'}"))
        self.assertFalse(await monitor.validate_pool(info,100))
        monitor.w3.eth.call=AsyncMock(side_effect=Web3RPCError("{'code': -32005, 'message': 'rate limited'}"))
        with self.assertRaises(Web3RPCError):
            await monitor.validate_pool(info,100)

    async def test_match_and_notification_commit_together(self):
        args = dict(platform_id=1, token_address=ADDRESS, token_name='Test', token_symbol='TEST',
                    deployer=CREATOR, match_type='BRAND', block_number=100, notification='test')
        with patch.object(state, 'enqueue_notification', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                database.save_platform_token_match(**args)
        self.assertEqual(self.rows('SELECT * FROM platform_token_matches'), [])
        self.assertTrue(database.save_platform_token_match(**args))
        self.assertFalse(database.save_platform_token_match(**args))
        self.assertEqual(len(self.rows('SELECT * FROM telegram_outbox')), 1)

    async def test_identity_notification_durable_before_confirmation_write(self):
        seed = dict(id=None, subject_key='website:test', creator='', member_addresses=[],
                    member_count=0, platform_types='', identity_urls=[])
        result = dict(subject_key='website:test', verification_status='VERIFIED', token_status='NONE')
        engine = Mock(); engine.enrich.return_value = result
        with patch.object(main, 'save_platform_identity_investigation', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                main.run_platform_identity_pipeline(engine=engine, protocols=[], standalone_seeds=[seed])
        self.assertEqual(len(self.rows('SELECT * FROM telegram_outbox')), 1)

    async def test_http_200_without_telegram_ok_is_not_success(self):
        response = Mock(status=200)
        response.text = AsyncMock(return_value=json.dumps({'ok': False, 'error_code': 429}))
        request = AsyncMock(); request.__aenter__.return_value = response
        session = Mock(); session.post.return_value = request
        context = AsyncMock(); context.__aenter__.return_value = session
        with patch.object(notifier, 'BOT_TOKEN', 'test'), patch.object(notifier, 'CHAT_ID', 'test'), \
             patch.object(notifier.aiohttp, 'ClientSession', return_value=context), \
             patch.object(notifier, 'TELEGRAM_RETRY_BASE_SECONDS', 0):
            with self.assertRaises(notifier.TelegramDeliveryError):
                await notifier.send_telegram('test')
            response.text.return_value = json.dumps({'ok': True, 'result': {'message_id': 1}})
            self.assertTrue(await notifier.send_telegram('test'))


if __name__ == '__main__':
    unittest.main()
