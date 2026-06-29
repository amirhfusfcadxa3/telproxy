#!/usr/bin/env python3

import os
import secrets
import subprocess
import time

PORT = int(os.environ.get("PORT", "8080"))


def run(cmd):
    print(f"[+] {cmd}", flush=True)
    subprocess.run(cmd, shell=True, check=True)


# نصب پیش‌نیازها
run("apt update")
run("apt install -y git curl build-essential libssl-dev zlib1g-dev")

# دانلود MTProxy
if not os.path.exists("/opt/MTProxy"):
    run("git clone https://github.com/TelegramMessenger/MTProxy /opt/MTProxy")

# کامپایل
run("cd /opt/MTProxy && make")

# دانلود فایل‌های رسمی
run(
    "curl -s https://core.telegram.org/getProxySecret "
    "-o /opt/MTProxy/objs/bin/proxy-secret"
)

run(
    "curl -s https://core.telegram.org/getProxyConfig "
    "-o /opt/MTProxy/objs/bin/proxy-multi.conf"
)

# ساخت secret
secret = secrets.token_hex(16)

print("=" * 50)
print("MTProxy configuration")
print(f"PORT   : {PORT}")
print(f"SECRET : {secret}")
print("=" * 50)

# اجرای MTProxy
proc = subprocess.Popen(
    [
        "./mtproto-proxy",
        "-u", "nobody",
        "-p", "8888",
        "-H", str(PORT),
        "-S", secret,
        "--aes-pwd", "proxy-secret",
        "proxy-multi.conf",
        "-M", "1",
    ],
    cwd="/opt/MTProxy/objs/bin",
)

print("[+] MTProxy started")

# زنده نگه داشتن کانتینر
try:
    while True:
        ret = proc.poll()
        if ret is not None:
            print(f"[!] MTProxy exited with code {ret}")
            break
        time.sleep(5)
except KeyboardInterrupt:
    proc.terminate()
