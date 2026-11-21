from types import SimpleNamespace
from app.risk import evaluate
S=SimpleNamespace(min_pool_liquidity_usd=1000,single_order_limit_usd=100,max_slippage_bps=300,max_price_impact_bps=300,max_gas_cost_usd=5)
def valid(): return dict(chain_id=1,adapter_verified=True,router_address='0xrouter',router_verified=True,token_address='0xtoken',liquidity_usd=1500,amount_usd=50,slippage_bps=100,price_impact_bps=100,estimated_gas_usd=1,security_check='passed',buy_simulation='passed',sell_simulation='passed')
def test_live_disabled_by_default(): assert not evaluate(valid(),S,{'enabled':True,'chain_id':1},False).allowed
def test_unknown_router_rejected():
 x=valid(); x['router_verified']=False
 assert not evaluate(x,S,{'enabled':True,'chain_id':1},True).allowed
def test_low_liquidity_rejected():
 x=valid(); x['liquidity_usd']=999
 assert not evaluate(x,S,{'enabled':True,'chain_id':1},True).allowed
def test_over_limit_rejected():
 x=valid(); x['amount_usd']=100.01
 assert not evaluate(x,S,{'enabled':True,'chain_id':1},True).allowed
def test_valid_signal_passes_pretrade_gates(): assert evaluate(valid(),S,{'enabled':True,'chain_id':1},True).allowed
