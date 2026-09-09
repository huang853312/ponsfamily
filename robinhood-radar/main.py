import asyncio
import logging
import signal

from alerts.telegram import Telegram
from analyzers.known_system_filter import KnownSystemFilter
from config import Settings
from monitors.blocks import BlockMonitor
from pipeline import Pipeline
from storage.database import Database

async def main():
    settings=Settings.load()
    logging.basicConfig(level=getattr(logging,settings.log_level.upper()),format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db=await Database(settings.database_path).open()
    pipeline=Pipeline(db,Telegram(settings.telegram_bot_token,settings.telegram_chat_id),KnownSystemFilter())
    task=asyncio.create_task(BlockMonitor(settings,db,pipeline.handle).run())
    loop=asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM): loop.add_signal_handler(sig,task.cancel)
    try: await task
    except asyncio.CancelledError: pass
    finally: await db.close()

if __name__ == "__main__": asyncio.run(main())

