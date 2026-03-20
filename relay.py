"""
relay.py — RemoteDesk Relay Server (TCP Murni)
Deploy ke Railway dengan TCP Proxy.

Railway setup:
  1. Settings → Networking → Add TCP Proxy → port 9001
  2. Railway beri alamat: ballast.proxy.rlwy.net:XXXXX
  3. Isi alamat itu di aplikasi RemoteDesk → Pengaturan

Jalankan lokal: python3 relay.py
Tidak perlu install library tambahan — pakai socket bawaan Python.
"""

import socket, threading, struct, json, time, os, sys, signal

RELAY_PORT = int(os.environ.get("PORT", 9001))
HOST       = "0.0.0.0"
ROOM_TTL   = 120   # detik sebelum room expired tanpa client join

rooms      = {}    # code -> {"server": sock, "client": sock, "created": time}
rooms_lock = threading.Lock()

# ── Utilitas ──────────────────────────────────────────────────────
def log(msg):
    import datetime
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def generate_code():
    import random, string
    with rooms_lock:
        while True:
            code = "".join(random.choices(string.digits, k=6))
            if code not in rooms:
                return code

# ── Protocol ──────────────────────────────────────────────────────
def send_msg(sock, data: bytes):
    sock.sendall(struct.pack(">I", len(data)) + data)

def recv_msg(sock) -> bytes | None:
    raw = _exact(sock, 4)
    if not raw: return None
    return _exact(sock, struct.unpack(">I", raw)[0])

def _exact(sock, n):
    buf = b""
    while len(buf) < n:
        c = sock.recv(n - len(buf))
        if not c: return None
        buf += c
    return buf

# ── Pipe dua arah ─────────────────────────────────────────────────
def pipe(src, dst, stop_evt):
    """Teruskan data raw dari src ke dst."""
    try:
        while not stop_evt.is_set():
            raw = _exact(src, 4)
            if not raw: break
            length = struct.unpack(">I", raw)[0]
            body   = _exact(src, length)
            if not body: break
            dst.sendall(raw + body)
    except:
        pass
    finally:
        stop_evt.set()

def relay_pair(code, srv_sock, cli_sock):
    log(f"[{code}] Relay dimulai")
    stop = threading.Event()
    t1 = threading.Thread(target=pipe, args=(srv_sock, cli_sock, stop), daemon=True)
    t2 = threading.Thread(target=pipe, args=(cli_sock, srv_sock, stop), daemon=True)
    t1.start(); t2.start()
    stop.wait()
    log(f"[{code}] Relay selesai")
    for s in [srv_sock, cli_sock]:
        try: s.close()
        except: pass
    with rooms_lock:
        rooms.pop(code, None)

# ── Handler koneksi ───────────────────────────────────────────────
def handle(conn, addr):
    ip = addr[0]
    try:
        conn.settimeout(15)
        raw = recv_msg(conn)
        if not raw: return conn.close()
        conn.settimeout(None)

        info = json.loads(raw.decode())
        role = info.get("role")
        code = info.get("code", "").strip()

        # ── PC Server daftar ──────────────────────────────────────
        if role == "server":
            code = generate_code()
            with rooms_lock:
                rooms[code] = {
                    "server":  conn,
                    "client":  None,
                    "created": time.time(),
                }
            log(f"[{code}] Server dari {ip}")
            send_msg(conn, json.dumps({"type": "registered", "code": code}).encode())

            # Tunggu client join
            deadline = time.time() + ROOM_TTL
            while time.time() < deadline:
                with rooms_lock:
                    room = rooms.get(code, {})
                    if room.get("client"):
                        cli = room["client"]
                        break
                time.sleep(0.3)
            else:
                log(f"[{code}] Timeout — tidak ada client")
                try: send_msg(conn, json.dumps({"type":"error","msg":"Timeout"}).encode())
                except: pass
                conn.close()
                with rooms_lock: rooms.pop(code, None)
                return

            # Beritahu server, mulai relay
            try: send_msg(conn, json.dumps({"type": "client_joined"}).encode())
            except: pass
            relay_pair(code, conn, cli)

        # ── PC Client join ────────────────────────────────────────
        elif role == "client":
            with rooms_lock:
                room = rooms.get(code)

            if not room or room.get("server") is None:
                send_msg(conn, json.dumps({
                    "type": "error",
                    "msg":  f"Kode {code} tidak ditemukan"
                }).encode())
                conn.close(); return

            with rooms_lock:
                if rooms[code]["client"] is not None:
                    send_msg(conn, json.dumps({
                        "type": "error", "msg": "Sesi sudah dipakai"
                    }).encode())
                    conn.close(); return
                rooms[code]["client"] = conn

            log(f"[{code}] Client dari {ip}")
            send_msg(conn, json.dumps({"type": "joined"}).encode())
            # Relay dikelola oleh thread server — client cukup tunggu

        else:
            send_msg(conn, json.dumps({"type":"error","msg":"Role tidak valid"}).encode())
            conn.close()

    except Exception as e:
        log(f"Error dari {ip}: {e}")
        try: conn.close()
        except: pass

# ── Cleanup room expired ──────────────────────────────────────────
def cleanup_loop():
    while True:
        time.sleep(60)
        now = time.time()
        with rooms_lock:
            expired = [c for c,r in rooms.items()
                       if r["client"] is None and now-r["created"] > ROOM_TTL]
        for c in expired:
            log(f"[{c}] Room expired, dihapus")
            with rooms_lock:
                rooms.pop(c, None)

# ── Main ──────────────────────────────────────────────────────────
def main():
    log(f"RemoteDesk Relay (TCP) — port {RELAY_PORT}")
    log(f"Rooms aktif: 0")

    threading.Thread(target=cleanup_loop, daemon=True).start()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, RELAY_PORT))
    srv.listen(50)
    log("Menunggu koneksi...")

    def _stop(s, f):
        log("Relay dihentikan.")
        srv.close(); sys.exit(0)
    signal.signal(signal.SIGINT,  _stop)
    signal.signal(signal.SIGTERM, _stop)

    while True:
        try:
            conn, addr = srv.accept()
            threading.Thread(target=handle, args=(conn, addr), daemon=True).start()
        except:
            break

if __name__ == "__main__":
    main()