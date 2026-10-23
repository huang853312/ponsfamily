import os
import tempfile
import unittest
from decimal import Decimal

os.environ.setdefault("COPY_STATE_FILE", os.path.join(tempfile.mkdtemp(), "state.json"))
import trader as T

TARGET = "0xfc9fdf943a988c8fc09830df38052ac9e320d4c8"
ME = "0x1111111111111111111111111111111111111111"
# Real buy calldata (0.1 ETH) from the target on Robinhood Chain.
BUY = ("0x4d819a2a"
       "00000000000000000000000000000000000000000000000000000000000000a0"
       "0000000000000000000000000000000000000000000000000000000000000000"
       "000000000000000000000000000000000000000000000000016345785d8a0000"
       "00000000000000000000000000000000000000000001e0fb4fdda234dbb43482"
       "000000000000000000000000000000000000000000000000000000006ada32cc"
       "0000000000000000000000000000000000000000000000000000000000000001"
       "0000000000000000000000000000000000000000000000000000000000000020"
       "0000000000000000000000000000000000000000000000000000000000000002"
       "0000000000000000000000000000000000000000000000000000000000000000"
       "0000000000000000000000000b7719c5b695fd990f165492ff5dbb760c89b1a3"
       "0000000000000000000000000000000000000000000000000000000000000000"
       "0000000000000000000000000000000000000000000000000000000000000000"
       "00000000000000000000000000000000000000000000000000000000000000c8"
       "000000000000000000000000e5e702641ea86f4ae6cc3cdaed2b886f976be044"
       "0000000000000000000000000000000000000000000000000000000000000140"
       "0000000000000000000000008366a39cc670b4001a1121b8f6a443a643e40951"
       "0000000000000000000000000000000000000000000000000000000000000000"
       "0000000000000000000000000000000000000000000000000000000000000000")


class Helpers(unittest.TestCase):
    def test_rewrite_keeps_route(self):
        out = T.rewrite_swap(BUY, TARGET, ME, 10 ** 16, 123, 999)
        self.assertEqual(len(out), len(BUY))
        self.assertEqual(T.word_int(out, 2), 10 ** 16)
        self.assertEqual(T.word_int(out, 3), 123)
        self.assertEqual(T.word_int(out, 4), 999)
        self.assertEqual(T.words(out)[5:], T.words(BUY)[5:])

    def test_rewrite_replaces_target_recipient(self):
        w = T.words(BUY); w[1] = "0" * 24 + TARGET[2:]
        cd = T.SWAP_SELECTOR + "".join(w)
        out = T.rewrite_swap(cd, TARGET, ME, 1, 0, 1)
        self.assertEqual(T.words(out)[1], "0" * 24 + ME[2:])

    def test_rewrite_rejects_other_calls(self):
        with self.assertRaises(ValueError):
            T.rewrite_swap("0xdeadbeef" + "0" * 64 * 5, TARGET, ME, 1, 0, 1)

    def test_scaled_min_out(self):
        self.assertEqual(T.scaled_min_out(1000, 100, 10, 15), 85)
        self.assertEqual(T.scaled_min_out(0, 100, 10, 15), 0)

    def test_sell_fraction(self):
        self.assertEqual(T.sell_fraction(50, 50), Decimal("0.5"))
        self.assertEqual(T.sell_fraction(96, 4), 1)
        self.assertEqual(T.sell_fraction(10, 0), 1)

    def test_classify(self):
        tpad = "0x" + "0" * 24 + TARGET[2:]
        other = "0x" + "0" * 64
        buy_logs = [{"address": "0xTok", "topics": [T.TRANSFER, other, tpad]}]
        self.assertEqual(T.classify({"value": "0x1"}, buy_logs, TARGET), ("buy", "0xtok"))
        sell_logs = [{"address": "0xTok", "topics": [T.TRANSFER, tpad, other]}]
        self.assertEqual(T.classify({"value": "0x0"}, sell_logs, TARGET), ("sell", "0xtok"))
        self.assertEqual(T.classify({"value": "0x0"}, buy_logs, TARGET), (None, None))


class Commands(unittest.TestCase):
    def setUp(self):
        self.t = T.Trader(rpc=None, state=T.load_state())

    def test_default_off(self):
        self.assertFalse(self.t.s["enabled"])
        self.assertIn(TARGET, self.t.s["targets"])

    def test_add_list_del(self):
        a = "0x2222222222222222222222222222222222222222"
        self.assertIn("已添加", self.t.command(f"/add {a} 大户"))
        self.assertIn("大户", self.t.command("/list"))
        self.assertIn("已删除", self.t.command(f"/del {a}"))

    def test_settings(self):
        self.t.command("/amount 0.02"); self.assertEqual(self.t.s["amount_eth"], "0.02")
        self.t.command(f"/amount {TARGET} 0.03"); self.assertEqual(self.t.s["targets"][TARGET]["amount_eth"], "0.03")
        self.t.command("/slip 10"); self.assertEqual(self.t.s["slippage_pct"], "10")
        self.assertIn("参数不对", self.t.command("/slip abc"))
        self.assertIn("参数不对", self.t.command("/add 0x123"))
        self.t.command("/on"); self.assertTrue(self.t.s["enabled"])
        self.t.command("/off"); self.assertFalse(self.t.s["enabled"])

    def test_unknown_ignored(self):
        self.assertIsNone(self.t.command("hello"))


if __name__ == "__main__":
    unittest.main()


class Safety(unittest.TestCase):
    def setUp(self):
        self.t = T.Trader(None, T.json.loads(T.json.dumps(T.DEFAULT_STATE)))

    def test_dryrun_needs_explicit_arg(self):
        self.t.s["dry_run"] = True
        self.t.command("/dryrun of")
        self.assertTrue(self.t.s["dry_run"])
        self.t.command("/dryrun off")
        self.assertFalse(self.t.s["dry_run"])

    def test_rejects_bad_amounts(self):
        for bad in ("/amount -1", "/amount inf", "/amount nan", "/max 0", "/daily Infinity"):
            self.assertIn("参数不对", self.t.command(bad))
        a = next(iter(self.t.s["targets"]))
        self.assertIn("参数不对", self.t.command(f"/amount {a} -0.5"))

    def test_cannot_add_router(self):
        self.assertIn("不能添加", self.t.command("/add 0x65050a9b7e5075a2ba5ced7b1b64ee66262c40dc x"))
