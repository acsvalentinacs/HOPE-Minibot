#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')
import asyncio
from api_server import HopeCoreAPIServer
from hope_core import HopeCore, HopeCoreConfig

async def main():
    config = HopeCoreConfig(mode='DRY')
    core = HopeCore(config)
    server = HopeCoreAPIServer(core, '127.0.0.1', 8200)
    
    # Start core in background
    async def run_core():
        core._running = True
        await asyncio.sleep(3600)  # Keep alive
    
    asyncio.create_task(run_core())
    await server.run()

if __name__ == "__main__":
    asyncio.run(main())
