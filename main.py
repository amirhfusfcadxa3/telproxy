#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess
import time
import threading
import socket
import secrets
from datetime import datetime

# ===================== نصب خودکار وابستگی‌ها =====================
def install_python_packages():
    required = ['psutil', 'flask']
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            print(f"[+] Installing Python package: {pkg}")
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--quiet', pkg])

install_python_packages()

import psutil
from flask import Flask, render_template_string

# ===================== تنظیم پورت‌ها =====================
PUBLIC_PORT = int(os.environ.get("PORT", "5000"))          # پورت عمومی (از Railway)
PUBLIC_SERVICE = os.environ.get("PUBLIC_SERVICE", "panel").lower()  # "panel" یا "proxy"

# پورت‌های پیش‌فرض
PANEL_PORT = int(os.environ.get("PANEL_PORT", "5000"))
PROXY_PORT = int(os.environ.get("PROXY_PORT", "1080"))

# تنظیم بر اساس سرویس عمومی
if PUBLIC_SERVICE == "panel":
    PANEL_PORT = PUBLIC_PORT
    if PROXY_PORT == PANEL_PORT:
        PROXY_PORT = 1081
elif PUBLIC_SERVICE == "proxy":
    PROXY_PORT = PUBLIC_PORT
    if PANEL_PORT == PROXY_PORT:
        PANEL_PORT = 5001
else:
    PANEL_PORT = PUBLIC_PORT
    if PROXY_PORT == PANEL_PORT:
        PROXY_PORT = 1080

print(f"[*] Panel port: {PANEL_PORT}, Proxy port: {PROXY_PORT}")
print(f"[*] Public service: {PUBLIC_SERVICE}")

# ===================== متغیرهای عمومی =====================
proxy_process = None
start_time = None
proxy_status = "Stopped"
stats_lock = threading.Lock()
PUBLIC_IP = None

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

# ===================== دریافت آی‌پی عمومی =====================
def get_public_ip():
    import urllib.request
    services = [
        'http://ifconfig.me/ip',
        'http://api.ipify.org',
        'http://icanhazip.com'
    ]
    for url in services:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                ip = resp.read().decode().strip()
                if ip:
                    return ip
        except:
            continue
    return os.environ.get("PUBLIC_IP", "UNKNOWN")

PUBLIC_IP = get_public_ip()

# ===================== منطق پروکسی SOCKS5 =====================
def forward(src, dst):
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            dst.sendall(data)
    except:
        pass
    finally:
        try:
            src.close()
        except:
            pass
        try:
            dst.close()
        except:
            pass

def handle_client(client):
    try:
        # greeting
        client.recv(2)
        nmethods = client.recv(1)[0]
        client.recv(nmethods)

        # no auth
        client.sendall(b"\x05\x00")

        # request
        ver, cmd, _, atyp = client.recv(4)

        if atyp == 1:  # IPv4
            addr = socket.inet_ntoa(client.recv(4))
        elif atyp == 3:  # domain
            length = client.recv(1)[0]
            addr = client.recv(length).decode()
        else:
            client.close()
            return

        port = int.from_bytes(client.recv(2), "big")

        remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        remote.connect((addr, port))

        client.sendall(
            b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00"
        )

        threading.Thread(target=forward, args=(client, remote), daemon=True).start()
        forward(remote, client)

    except Exception:
        try:
            client.close()
        except:
            pass

def run_proxy():
    global proxy_process, start_time, proxy_status
    with stats_lock:
        if proxy_process and proxy_process.poll() is None:
            log("Proxy already running")
            return
        if proxy_process:
            proxy_process = None

        log(f"Starting SOCKS5 proxy on port {PROXY_PORT}...")
        try:
            # پروکسی را در یک ترد جداگانه اجرا می‌کنیم (چون socket.accept() blocking است)
            def proxy_loop():
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind(("0.0.0.0", PROXY_PORT))
                server.listen(100)
                log(f"SOCKS5 proxy listening on port {PROXY_PORT}")

                while True:
                    try:
                        client, _ = server.accept()
                        threading.Thread(target=handle_client, args=(client,), daemon=True).start()
                    except Exception as e:
                        log(f"Proxy accept error: {e}")
                        break

            proxy_process = threading.Thread(target=proxy_loop, daemon=True)
            proxy_process.start()
            start_time = datetime.now()
            proxy_status = "Running"
            log(f"Proxy started on port {PROXY_PORT}")
        except Exception as e:
            log(f"Failed to start proxy: {e}")
            proxy_status = f"Error: {e}"

def keepalive_loop():
    """بررسی هر ۵ ثانیه و ری‌استارت در صورت لزوم"""
    while True:
        with stats_lock:
            if proxy_process is None or not proxy_process.is_alive():
                if proxy_process and not proxy_process.is_alive():
                    log("Proxy thread died. Restarting...")
                run_proxy()
        time.sleep(5)

# ===================== آمار سیستم =====================
def get_network_stats():
    try:
        net = psutil.net_io_counters()
        return net.bytes_sent, net.bytes_recv
    except:
        try:
            with open("/proc/net/dev", "r") as f:
                lines = f.readlines()
            sent = recv = 0
            for line in lines[2:]:
                parts = line.split()
                if "lo" in parts[0]:
                    continue
                if len(parts) >= 10:
                    recv += int(parts[1])
                    sent += int(parts[9])
            return sent, recv
        except:
            return 0, 0

def get_system_stats():
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        return {"cpu": cpu, "memory_percent": mem.percent}
    except:
        return {"cpu": 0, "memory_percent": 0}

# ===================== پنل وب =====================
def create_panel():
    app = Flask(__name__)

    HTML_TEMPLATE = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>SOCKS5 Proxy Monitor</title>
        <meta charset="UTF-8">
        <meta http-equiv="refresh" content="5">
        <style>
            body { font-family: sans-serif; background: #1e1e2f; color: #fff; padding: 20px; }
            .card { background: #2d2d44; padding: 15px; border-radius: 8px; margin: 10px 0; }
            .value { font-size: 24px; font-weight: bold; color: #4fc3f7; }
            .label { font-size: 14px; color: #aaa; }
            .status-running { color: #4caf50; }
            .status-stopped { color: #f44336; }
            .row { display: flex; flex-wrap: wrap; gap: 20px; }
            .col { flex: 1; min-width: 200px; }
            a { color: #4fc3f7; text-decoration: none; }
            a:hover { text-decoration: underline; }
            .link-box { background: #1a1a2e; padding: 10px; border-radius: 5px; word-break: break-all; font-size: 14px; }
        </style>
    </head>
    <body>
        <h1>🔒 SOCKS5 Proxy Dashboard</h1>
        <div class="card">
            <div class="row">
                <div class="col">
                    <div class="label">Status</div>
                    <div class="value status-{{ status_class }}">{{ status }}</div>
                </div>
                <div class="col">
                    <div class="label">Uptime</div>
                    <div class="value">{{ uptime }}</div>
                </div>
                <div class="col">
                    <div class="label">Proxy Port</div>
                    <div class="value">{{ proxy_port }}</div>
                </div>
            </div>
        </div>
        <div class="card">
            <div class="row">
                <div class="col">
                    <div class="label">Total Traffic Sent</div>
                    <div class="value">{{ traffic_sent }}</div>
                </div>
                <div class="col">
                    <div class="label">Total Traffic Received</div>
                    <div class="value">{{ traffic_recv }}</div>
                </div>
            </div>
        </div>
        <div class="card">
            <div class="row">
                <div class="col">
                    <div class="label">CPU Usage</div>
                    <div class="value">{{ cpu }}%</div>
                </div>
                <div class="col">
                    <div class="label">Memory Usage</div>
                    <div class="value">{{ mem_percent }}%</div>
                </div>
            </div>
        </div>
        <div class="card">
            <div class="label">Proxy Connection URL (SOCKS5)</div>
            <div class="link-box">
                <a href="{{ proxy_url }}" target="_blank">{{ proxy_url }}</a>
            </div>
            <br>
            <div class="label">Server Address</div>
            <div class="link-box">{{ public_ip }}:{{ proxy_port }}</div>
            <div class="label">Note: This proxy has no authentication.</div>
        </div>
    </body>
    </html>
    """

    @app.route('/')
    def index():
        global proxy_status, start_time
        with stats_lock:
            status = proxy_status
            uptime = "N/A"
            if start_time and status == "Running":
                delta = datetime.now() - start_time
                uptime = str(delta).split('.')[0]
            elif start_time:
                uptime = "Stopped"

        sent, recv = get_network_stats()
        def human_readable(b):
            for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
                if b < 1024.0:
                    return f"{b:.2f} {unit}"
                b /= 1024.0
            return f"{b:.2f} PB"

        sys_stats = get_system_stats()
        proxy_url = f"socks5://{PUBLIC_IP}:{PROXY_PORT}"

        return render_template_string(
            HTML_TEMPLATE,
            status=status,
            status_class="running" if status == "Running" else "stopped",
            uptime=uptime,
            proxy_port=PROXY_PORT,
            traffic_sent=human_readable(sent),
            traffic_recv=human_readable(recv),
            cpu=sys_stats.get("cpu", 0),
            mem_percent=sys_stats.get("memory_percent", 0),
            public_ip=PUBLIC_IP,
            proxy_url=proxy_url
        )

    def run_flask():
        app.run(host='0.0.0.0', port=PANEL_PORT, debug=False, use_reloader=False)

    threading.Thread(target=run_flask, daemon=True).start()
    log(f"Panel started on port {PANEL_PORT}")

# ===================== نمایش لینک‌ها در لاگ =====================
def print_links():
    log("=" * 60)
    log("SOCKS5 Proxy is running. Use the following links:")
    panel_url = f"http://{PUBLIC_IP}:{PANEL_PORT}"
    log(f"Panel URL: {panel_url}")
    proxy_url = f"socks5://{PUBLIC_IP}:{PROXY_PORT}"
    log(f"Proxy URL (SOCKS5): {proxy_url}")
    log(f"Server: {PUBLIC_IP}:{PROXY_PORT}")
    log("=" * 60)

# ===================== تابع اصلی =====================
def main():
    log("=== SOCKS5 Proxy Auto-Setup & Monitor ===")

    # راه‌اندازی پروکسی
    run_proxy()

    # Keepalive
    threading.Thread(target=keepalive_loop, daemon=True).start()

    # پنل
    create_panel()

    # چاپ لینک‌ها
    print_links()

    log("All services are running. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(10)
            with stats_lock:
                if proxy_process and not proxy_process.is_alive():
                    log("Proxy thread died, will restart soon...")
    except KeyboardInterrupt:
        log("Shutting down...")
        log("Done.")

if __name__ == "__main__":
    main()
