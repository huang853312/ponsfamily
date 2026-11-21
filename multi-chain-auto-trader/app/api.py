import hmac, os
from fastapi import FastAPI, Header, HTTPException
from .settings import Settings,load_yaml,validate_settings
from .database import Database
from .engine import TradingEngine
settings=Settings.from_env(); validate_settings(settings)
chain_doc=load_yaml(settings.chains_file)
chains={str(v.get('chain_id')):v for v in chain_doc.get('chains',{}).values() if v.get('chain_id')}
db=Database(settings.database_path); engine=TradingEngine(settings,db,chains)
app=FastAPI(title='Multi-Chain Auto Trader',docs_url=None,redoc_url=None)
def authorize(value):
    expected=settings.signal_api_token
    if not expected or not value or not hmac.compare_digest(value,expected): raise HTTPException(status_code=401,detail='Unauthorized')
@app.get('/health')
def health(): return {'status':'ok','trading_enabled':settings.trading_enabled,'chains_configured':len(chains),'execution_adapter':'not-installed'}
@app.post('/signals')
def receive_signal(payload: dict,authorization: str|None=Header(default=None)):
    authorize(authorization); return engine.handle(payload)
@app.post('/kill')
def kill(authorization: str|None=Header(default=None)):
    authorize(authorization); os.makedirs('/var/lib/multi-chain-auto-trader',exist_ok=True)
    open('/var/lib/multi-chain-auto-trader/KILL_SWITCH','w').write('STOP')
    return {'status':'kill_switch_engaged'}
@app.post('/resume')
def resume(authorization: str|None=Header(default=None)):
    authorize(authorization); path='/var/lib/multi-chain-auto-trader/KILL_SWITCH'
    if os.path.exists(path): os.remove(path)
    return {'status':'kill_switch_cleared'}
