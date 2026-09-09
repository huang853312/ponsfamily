import asyncio
import logging
import signal

from alerts.telegram import Telegram
from analyzers.known_system_filter import KnownSystemFilter
from analyzers.deployer import DeployerTracer
from config import Settings
from monitors.blocks import BlockMonitor
from monitors.contracts import ContractCreationMonitor
from pipeline import Pipeline, ObservationManager
from analysis.activity import ActivityWorker
from rpc import RPCLimiter
from storage.database import Database

async def main():
    settings=Settings.load()
    logging.basicConfig(level=getattr(logging,settings.log_level.upper()),format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db=await Database(settings.database_path).open()
    rpc_limiter=RPCLimiter(settings.max_rpc_concurrency)
    tracer=DeployerTracer(settings.trace_enabled,settings.max_trace_concurrency,rpc_limiter)
    telegram=Telegram(settings.telegram_bot_token,settings.telegram_chat_id)
    pipeline=Pipeline(db,telegram,KnownSystemFilter(),tracer,settings.observation_minutes,rpc_limiter)
    tasks=[]
    if settings.v2_factories+settings.v3_factories+settings.v4_managers:
        tasks.append(asyncio.create_task(BlockMonitor(settings,db,pipeline.handle,rpc_limiter).run(),name="pool-monitor"))
    if settings.contract_monitor_enabled:
        tasks.append(asyncio.create_task(ContractCreationMonitor(settings,db,pipeline.handle_contract,tracer,rpc_limiter).run(),name="contract-monitor"))
    tasks.append(asyncio.create_task(ActivityWorker(settings,db,rpc_limiter,telegram=telegram).run(),name="activity-worker"))
    tasks.append(asyncio.create_task(ObservationManager(db).run(),name="observation-manager"))
    loop=asyncio.get_running_loop()
    def stop():
        for task in tasks:task.cancel()
    for sig in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(sig,stop)
    try:await asyncio.gather(*tasks)
    except asyncio.CancelledError:pass
    finally:
        stop();await asyncio.gather(*tasks,return_exceptions=True)
        await db.close()

if __name__ == "__main__": asyncio.run(main())
