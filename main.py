import asyncio
import logging
import os
import sys
import warnings
from aiohttp import web

warnings.filterwarnings("ignore")

from app.telegram_bot import build_application

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Silence HTTP request noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)

async def handle_ping(request):
    """Render Web Service Healthcheck Endpoint."""
    return web.Response(text="Learn with HiM Quiz Book Bot is Online & Active!")

async def start_web_server():
    """Starts a lightweight web server for Render keep-alive calls."""
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/ping", handle_ping)
        
    port = int(os.getenv("PORT", "8080"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"  Keep-Alive Web Server running on port {port}")

async def render_self_ping_loop():
    """Background heartbeat loop that runs every 5 minutes to keep Render active."""
    import httpx
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if not render_url:
        return
    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.sleep(300) # Ping every 5 minutes
            try:
                await client.get(f"{render_url}/ping")
            except Exception:
                pass

async def run_bot():
    print("==================================================")
    print("  Learn with HiM Quiz Book Bot Engine")
    print("==================================================")
        
    # Start Keep-Alive Web Server for Render
    await start_web_server()
    asyncio.create_task(render_self_ping_loop())
    
    app = build_application()
        
    await app.initialize()
    await app.start()
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.updater.start_polling(drop_pending_updates=True)
        
    print("  Bot is online, synchronized, and listening!")
    print("==================================================")
        
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        print("\n  Shutting down bot gracefully...")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

def main():
    # Python 3.14+ Event Loop Fix
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        print("  Bot offline.")

if __name__ == "__main__":
    main()