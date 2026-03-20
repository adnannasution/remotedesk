"""
relay.py — RemoteDesk Relay Server (WebSocket)
Kompatibel dengan Railway, Render, Fly.io, VPS biasa.

Deploy ke Railway:
  1. Buat repo GitHub, upload file ini
  2. New Project di Railway → Deploy from GitHub
  3. Railway otomatis detect Python dan jalankan
  4. Settings → Networking → Generate Domain
  5. Catat domain Railway (misal: relay-xxx.up.railway.app)

Install lokal: pip install websockets
Jalankan lokal: python3 relay.py
"""

import asyncio
import json
import os
import time
import websockets
from websockets.server import serve

PORT      = int(os.environ.get("PORT", 8765))  # Railway inject PORT otomatis
ROOM_TTL  = 120  # detik sebelum room expired

# ── Room manager ──────────────────────────────────────────────────
rooms = {}  # code -> {"server": ws, "client": ws, "created": time}

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
async def pipe(src, dst, label):
    try:
        async for msg in src:
            await dst.send(msg)
    except:
        pass

# ── Handler koneksi ───────────────────────────────────────────────
async def handler(ws):
    ip = ws.remote_address[0] if ws.remote_address else "?"
    try:
        # Terima pesan pertama: identitas role
        raw  = await asyncio.wait_for(ws.recv(), timeout=15)
        info = json.loads(raw)
        role = info.get("role")
        code = info.get("code", "").strip()

        # ── PC Server daftar, minta kode ──────────────────────────
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
                await ws.send(json.dumps({"type": "error", "msg": "Timeout"}))
                rooms.pop(code, None)
                return

            client_ws = rooms[code]["client"]
            await ws.send(json.dumps({"type": "client_joined"}))
            log(f"[{code}] Relay dimulai")

            # Relay dua arah serentak
            await asyncio.gather(
                pipe(ws,        client_ws, "srv→cli"),
                pipe(client_ws, ws,        "cli→srv"),
            )
            log(f"[{code}] Relay selesai")
            rooms.pop(code, None)

        # ── PC Client minta join ───────────────────────────────────
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
                    "type": "error",
                    "msg":  "Sesi sudah dipakai"
                }))
                return

            room["client"] = ws
            log(f"[{code}] Client dari {ip} join")
            await ws.send(json.dumps({"type": "joined"}))
            # Tunggu relay selesai (dikelola thread server)
            await ws.wait_closed()

        else:
            await ws.send(json.dumps({"type": "error", "msg": "Role tidak valid"}))

    except asyncio.TimeoutError:
        log(f"Timeout dari {ip}")
    except Exception as e:
        log(f"Error dari {ip}: {e}")

# ── Cleanup room expired ──────────────────────────────────────────
async def cleanup():
    while True:
        await asyncio.sleep(60)
        now     = time.time()
        expired = [c for c, r in rooms.items()
                   if r["client"] is None and now - r["created"] > ROOM_TTL]
        for c in expired:
            log(f"[{c}] Room expired")
            rooms.pop(c, None)

# ── Status endpoint sederhana (HTTP GET /) ────────────────────────
async def status_handler(ws):
    # WebSocket tidak untuk status, tapi Railway butuh health check HTTP
    # Gunakan proses terpisah atau biarkan Railway pakai port check
    pass

# ── Main ──────────────────────────────────────────────────────────
async def main():
    log(f"RemoteDesk Relay (WebSocket) — port {PORT}")
    asyncio.create_task(cleanup())
    async with serve(handler, "0.0.0.0", PORT,
                     ping_interval=20, ping_timeout=60,
                     max_size=10 * 1024 * 1024):  # max 10MB per pesan
        log("Siap menerima koneksi...")
        await asyncio.Future()  # jalan selamanya

if __name__ == "__main__":
    asyncio.run(main())
