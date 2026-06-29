#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess
import time
import secrets
import threading
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

# ===================== تنظیم هوشمند پورت‌ها =====================
# پورت پنل: اولویت با PANEL_PORT، سپس PORT (برای Railway)، در نهایت 5000
PANEL_PORT = int(os.environ.get("PANEL_PORT") or os.environ.get("PORT", "5000"))
# پورت پروکسی: اولویت با PROXY_PORT، در غیر این صورت 8080
PROXY_PORT = int(os.environ.get("PROXY_PORT", "8080"))

# اگر پورت‌ها با هم تداخل داشتند، پورت پروکسی را به 8081 تغییر بده
if PANEL_PORT == PROXY_PORT:
    PROXY_PORT = 8081
    print(f"[!] Port conflict detected: panel and proxy both on {PANEL_PORT}. Proxy moved to {PROXY_PORT}")

# (اختیاری) اگر کاربر خواست پورت دیگری برای پروکسی، می‌تواند متغیر PROXY_PORT را تنظیم کند

SECRET = os.environ.get("SECRET", secrets.token_hex(16))

proxy_process = None
start_time = None
proxy_status = "Stopped"
stats_lock = threading.Lock()

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

# ===================== نصب پیش‌نیازهای سیستمی و MTProxy =====================
def run_cmd(cmd, check=True):
    log(f"Running: {cmd}")
    subprocess.run(cmd, shell=True, check=check)

def setup_system_and_proxy():
    run_cmd("apt update -qq")
    run_cmd("apt install -y -qq git curl build-essential libssl-dev zlib1g-dev")

    if not os.path.exists("/opt/MTProxy"):
        run_cmd("git clone https://github.com/TelegramMessenger/MTProxy /opt/MTProxy")

    run_cmd("cd /opt/MTProxy && make")

    run_cmd("curl -s https://core.telegram.org/getProxySecret -o /opt/MTProxy/objs/bin/proxy-secret")
    run_cmd("curl -s https://core.telegram.org/getProxyConfig -o /opt/MTProxy/objs/bin/proxy-multi.conf")

# ===================== مدیریت پروکسی (Keepalive) =====================
def start_proxy():
    global proxy_process, start_time, proxy_status
    with stats_lock:
        if proxy_process and proxy_process.poll() is None:
            log("Proxy already running")
            return
        if proxy_process:
            proxy_process = None

        work_dir = "/opt/MTProxy/objs/bin"
        if not os.path.exists(work_dir):
            log(f"ERROR: {work_dir} not found! Run setup first.")
            proxy_status = "Error: Missing MTProxy"
            return

        cmd = [
            "./mtproto-proxy",
            "-u", "nobody",
            "-p", "8888",
            "-H", str(PROXY_PORT),
            "-S", SECRET,
            "--aes-pwd", "proxy-secret",
            "proxy-multi.conf",
            "-M", "1"
        ]
        try:
            proxy_process = subprocess.Popen(
                cmd,
                cwd=work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            start_time = datetime.now()
            proxy_status = "Running"
            log(f"Proxy started on port {PROXY_PORT} with secret {SECRET}")
            threading.Thread(target=read_proxy_output, daemon=True).start()
        except Exception as e:
            log(f"Failed to start proxy: {e}")
            proxy_status = f"Error: {e}"

def read_proxy_output():
    global proxy_process
    while proxy_process and proxy_process.stdout:
        line = proxy_process.stdout.readline()
        if line:
            log(f"PROXY: {line.strip()}")
        else:
            break

def keepalive_loop():
    while True:
        with stats_lock:
            if proxy_process is None or proxy_process.poll() is not None:
                if proxy_process and proxy_process.poll() is not None:
                    log(f"Proxy exited with code {proxy_process.returncode}. Restarting...")
                start_proxy()
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
        <title>MTProxy Monitor</title>
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
        <h1>📡 MTProxy Dashboard</h1>
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
            <div class="label">Proxy Connection Link (tap to add in Telegram)</div>
            <div class="link-box">
                <a href="{{ tg_link }}" target="_blank">{{ tg_link }}</a>
            </div>
            <br>
            <div class="label">Server Address</div>
            <div class="link-box">{{ public_ip }}:{{ proxy_port }}</div>
            <div class="label">Secret</div>
            <div class="link-box">{{ secret }}</div>
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
        tg_link = f"tg://proxy?server={PUBLIC_IP}&port={PROXY_PORT}&secret={SECRET}"

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
            secret=SECRET,
            tg_link=tg_link
        )

    def run_flask():
        app.run(host='0.0.0.0', port=PANEL_PORT, debug=False, use_reloader=False)

    threading.Thread(target=run_flask, daemon=True).start()
    log(f"Panel started on port {PANEL_PORT}")

# ===================== نمایش لینک‌ها در لاگ =====================
def print_links():
    log("=" * 60)
    log("MTProxy is running. Use the following links:")
    panel_url = f"http://{PUBLIC_IP}:{PANEL_PORT}"
    log(f"Panel URL: {panel_url}")
    tg_link = f"tg://proxy?server={PUBLIC_IP}&port={PROXY_PORT}&secret={SECRET}"
    log(f"Proxy Link (add to Telegram): {tg_link}")
    log(f"Server: {PUBLIC_IP}:{PROXY_PORT}")
    log(f"Secret: {SECRET}")
    log("=" * 60)

# ===================== تابع اصلی =====================
def main():
    log("=== MTProxy Auto-Setup & Monitor ===")

    # نصب و کامپایل (در صورت لزوم)
    if not os.path.exists("/opt/MTProxy/objs/bin/mtproto-proxy"):
        log("First-time setup: installing system dependencies and compiling MTProxy...")
        setup_system_and_proxy()
    else:
        log("MTProxy already compiled. Skipping system setup.")

    if not os.environ.get("SECRET"):
        os.environ["SECRET"] = SECRET

    # راه‌اندازی پروکسی
    start_proxy()

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
                if proxy_process and proxy_process.poll() is not None:
                    log(f"Proxy exited (code {proxy_process.returncode}), will restart soon...")
    except KeyboardInterrupt:
        log("Shutting down...")
        if proxy_process:
            proxy_process.terminate()
            time.sleep(1)
            if proxy_process.poll() is None:
                proxy_process.kill()
        log("Done.")

if __name__ == "__main__":
    main()
