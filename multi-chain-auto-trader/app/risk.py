from dataclasses import dataclass
@dataclass
class RiskDecision:
    allowed: bool
    reason: str

def evaluate(signal: dict, settings, chain_config: dict, trading_enabled: bool) -> RiskDecision:
    if not trading_enabled: return RiskDecision(False,'TRADING_ENABLED is false; execution locked')
    if not chain_config or not chain_config.get('enabled'): return RiskDecision(False,'Chain is not explicitly enabled')
    if int(chain_config.get('chain_id',-1)) != int(signal.get('chain_id',-2)): return RiskDecision(False,'Chain ID mismatch')
    if not signal.get('adapter_verified'): return RiskDecision(False,'DEX/factory adapter is not verified')
    if not signal.get('router_address') or not signal.get('router_verified'): return RiskDecision(False,'Router missing or not allowlisted')
    if not signal.get('token_address'): return RiskDecision(False,'Token address missing')
    if float(signal.get('liquidity_usd',0)) < settings.min_pool_liquidity_usd: return RiskDecision(False,'Pool liquidity below $1,000')
    if float(signal.get('amount_usd',0)) <= 0 or float(signal['amount_usd']) > settings.single_order_limit_usd: return RiskDecision(False,'Order violates $100 single-order cap')
    if int(signal.get('slippage_bps',10**9)) > settings.max_slippage_bps: return RiskDecision(False,'Slippage exceeds 3%')
    if float(signal.get('price_impact_bps',10**9)) > settings.max_price_impact_bps: return RiskDecision(False,'Price impact exceeds cap')
    if float(signal.get('estimated_gas_usd',10**9)) > settings.max_gas_cost_usd: return RiskDecision(False,'Estimated gas exceeds cap')
    if signal.get('security_check') != 'passed': return RiskDecision(False,'Token security checks missing or failed')
    if signal.get('buy_simulation') != 'passed' or signal.get('sell_simulation') != 'passed': return RiskDecision(False,'Buy and sell simulations must both pass')
    return RiskDecision(True,'All configured pre-trade checks passed')
