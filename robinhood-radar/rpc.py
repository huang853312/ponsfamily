"""One process-wide bound for every JSON-RPC request."""
import asyncio
import inspect
from contextlib import asynccontextmanager

class RPCLimiter:
    def __init__(self,limit):
        self.limit=limit;self._semaphore=asyncio.Semaphore(limit)
        self.active=0;self.peak=0
    @asynccontextmanager
    async def slot(self):
        async with self._semaphore:
            self.active+=1;self.peak=max(self.peak,self.active)
            try:yield
            finally:self.active-=1
    async def call(self,awaitable):
        acquired=False
        try:
            await self._semaphore.acquire();acquired=True
            self.active+=1;self.peak=max(self.peak,self.active)
            return await awaitable
        except BaseException:
            if not acquired and inspect.iscoroutine(awaitable):awaitable.close()
            raise
        finally:
            if acquired:self.active-=1;self._semaphore.release()
