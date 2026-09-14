import asyncio,sqlite3,tempfile,unittest
from unittest.mock import AsyncMock,Mock,patch
import monitors.blocks as m
from hexbytes import HexBytes
class ScanTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.p=patch.object(m,'DB_PATH',self.temp.name+'/radar.db');self.p.start();self.monitor=m.HyperEVMBlockMonitor()
    def tearDown(self):self.p.stop();self.temp.cleanup()
    async def test_restart_uses_saved_progress(self):
        self.assertEqual(self.monitor.scan_position(10),10)
        self.monitor.get_contract_creations=AsyncMock(return_value=[])
        await self.monitor.scan_block(10,AsyncMock())
        self.assertEqual(m.HyperEVMBlockMonitor().scan_position(999),11)
    async def test_failure_does_not_advance(self):
        self.monitor.scan_position(10);self.monitor.get_contract_creations=AsyncMock(side_effect=RuntimeError('rate limited'))
        with self.assertRaises(RuntimeError):await self.monitor.scan_block(10,AsyncMock())
        self.assertEqual(self.monitor.scan_position(999),10)
        self.monitor.get_contract_creations=AsyncMock(return_value=[{}])
        with self.assertRaises(RuntimeError):await self.monitor.scan_block(10,AsyncMock(side_effect=RuntimeError('handler failed')))
        self.assertEqual(self.monitor.scan_position(999),10)
    async def test_logs_failure_propagates(self):
        self.monitor.w3=Mock();self.monitor.w3.eth.get_logs=AsyncMock(side_effect=RuntimeError('rate limited'))
        with self.assertRaises(RuntimeError):await self.monitor.get_logs(10)
    async def test_receipt_failure_propagates(self):
        self.monitor.w3=Mock();block=Mock();block.transactions=[{'to':None,'from':'0x'+'a'*40,'hash':b'1'*32}]
        self.monitor.w3.eth.get_block=AsyncMock(return_value=block);self.monitor.w3.eth.get_transaction_receipt=AsyncMock(side_effect=RuntimeError('timeout'))
        with patch.object(m,'load_deployers',return_value={}):
            with self.assertRaises(RuntimeError):await self.monitor.get_contract_creations(10)
    async def test_pool_requires_factory_reverse_mapping(self):
        info={'pool':'0x'+'1'*40,'factory':'0x'+'2'*40,'token0':'0x'+'3'*40,'token1':'0x'+'4'*40,'type':'V2_PAIR'}
        self.monitor.w3.eth.get_code=AsyncMock(return_value=b'code')
        word=lambda a:HexBytes(bytes.fromhex(a[2:]).rjust(32,b'\0'))
        self.monitor.w3.eth.call=AsyncMock(side_effect=[word(info['factory']),word(info['token0']),word(info['token1']),word(info['pool'])])
        self.assertTrue(await self.monitor.validate_pool(info,10))
        self.monitor.w3.eth.call=AsyncMock(side_effect=[word(info['factory']),word(info['token0']),word(info['token1']),word('0x'+'5'*40)])
        self.assertFalse(await self.monitor.validate_pool(info,10))
if __name__=='__main__':unittest.main()
