"""
relay.py — RemoteDesk Relay Server (WebSocket)
Kompatibel Railway, Render, Fly.io, VPS biasa.
"""

import asyncio, json, os, time, signal, sys
import websockets
from websockets.server import serve

PORT     = int(os.environ.get("PORT", 8765))
ROOM_TTL = 120

rooms = {}

def generate_code():
    import random, string
    while True:
        code = "".join(random.choices(string.digits, k=6))
        if code not in rooms:
            return code

def log(msg):
    import datetime
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# ── Relay dua arah ────────────────────────────────────────────────
async def pipe(src, dst):
    try:
        async for msg in src:
            await dst.send(msg)
    except:
        pass

# ── Handler koneksi ───────────────────────────────────────────────
async def handler(ws):
    ip = ws.remote_address[0] if ws.remote_address else "?"

    # Cek apakah ini HTTP health check dari Railway
    # Railway kirim ping HTTP, websockets library handle upgrade otomatis
    # kalau bukan WebSocket upgrade, koneksi akan ditolak library — itu normal

    try:
        raw  = await asyncio.wait_for(ws.recv(), timeout=15)
        info = json.loads(raw if isinstance(raw, str) else raw.decode())
        role = info.get("role")
        code = info.get("code", "").strip()

        if role == "server":
            code = generate_code()
            rooms[code] = {"server": ws, "client": None, "created": time.time()}
            log(f"[{code}] Server dari {ip}")

            await ws.send(json.dumps({"type": "registered", "code": code}))

            # Tunggu client join
            deadline = time.time() + ROOM_TTL
            while time.time() < deadline:
                if rooms.get(code, {}).get("client") is not None:
                    break
                await asyncio.sleep(0.5)
            else:
                log(f"[{code}] Timeout")
                try: await ws.send(json.dumps({"type":"error","msg":"Timeout"}))
                except: pass
                rooms.pop(code, None)
                return

            client_ws = rooms[code]["client"]
            try: await ws.send(json.dumps({"type": "client_joined"}))
            except: pass
            log(f"[{code}] Relay dimulai")

            await asyncio.gather(
                pipe(ws, client_ws),
                pipe(client_ws, ws),
            )
            log(f"[{code}] Relay selesai")
            rooms.pop(code, None)

        elif role == "client":
            if not code or code not in rooms:
                await ws.send(json.dumps({
                    "type": "error",
                    "msg":  f"Kode {code} tidak ditemukan"
                }))
                return

            room = rooms[code]
            if room["client"] is not None:
                await ws.send(json.dumps({
                    "type": "error", "msg": "Sesi sudah dipakai"
                }))
                return

            room["client"] = ws
            log(f"[{code}] Client dari {ip}")
            await ws.send(json.dumps({"type": "joined"}))
            await ws.wait_closed()

        else:
            await ws.send(json.dumps({"type":"error","msg":"Role tidak valid"}))

    except asyncio.TimeoutError:
        log(f"Timeout dari {ip}")
    except Exception as e:
        log(f"Error dari {ip}: {e}")

# ── Cleanup ───────────────────────────────────────────────────────
async def cleanup():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        expired = [c for c,r in list(rooms.items())
                   if r["client"] is None and now-r["created"] > ROOM_TTL]
        for c in expired:
            log(f"[{c}] Room expired")
            rooms.pop(c, None)

# ── Health check HTTP sederhana (untuk Railway) ───────────────────
async def health_check(reader, writer):
    try:
        await reader.read(1024)
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 2\r\n"
            b"\r\n"
            b"OK"
        )
        writer.write(response)
        await writer.drain()
    except:
        pass
    finally:
        writer.close()

# ── Main ──────────────────────────────────────────────────────────
async def main():
    log(f"RemoteDesk Relay — port {PORT}")
    asyncio.create_task(cleanup())

    # WebSocket server
    ws_server = await serve(
        handler, "0.0.0.0", PORT,
        ping_interval=20,
        ping_timeout=60,
        max_size=10 * 1024 * 1024,
        # process_request untuk handle HTTP health check
        process_request=process_request,
    )
    log("Siap menerima koneksi...")
    await asyncio.Future()

async def process_request(connection, request):
    """
    Intercept HTTP request sebelum WebSocket upgrade.
    Railway health check kirim HTTP GET — kita balas 200 OK.
    Request WebSocket upgrade dibiarkan lanjut (return None).
    """
    if request.headers.get("Upgrade", "").lower() != "websocket":
        # Ini HTTP biasa (health check) — balas 200 OK
        from websockets.http11 import Response
        return Response(
            status_code=200,
            headers={"Content-Type": "text/plain", "Content-Length": "2"},
            body=b"OK",
        )
    # WebSocket upgrade — biarkan lanjut normal
    return None

if __name__ == "__main__":
    asyncio.run(main())