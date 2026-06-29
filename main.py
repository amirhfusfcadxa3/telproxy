import os
import subprocess
import secrets
import socket

PORT = 8080

def run(cmd):
    print(f"[+] {cmd}")
    subprocess.run(cmd, shell=True, check=True)

# بررسی دسترسی روت
if os.geteuid() != 0:
    print("این اسکریپت باید با sudo یا root اجرا شود.")
    exit(1)

# نصب پیش‌نیازها
run("apt update")
run("apt install -y git curl build-essential libssl-dev zlib1g-dev")

# دانلود MTProxy
if not os.path.exists("/opt/MTProxy"):
    run("git clone https://github.com/TelegramMessenger/MTProxy /opt/MTProxy")

# کامپایل
run("cd /opt/MTProxy && make")

# دانلود فایل‌های رسمی تلگرام
run("curl -s https://core.telegram.org/getProxySecret -o /opt/MTProxy/objs/bin/proxy-secret")
run("curl -s https://core.telegram.org/getProxyConfig -o /opt/MTProxy/objs/bin/proxy-multi.conf")

# ساخت Secret
secret = secrets.token_hex(16)

# گرفتن IP عمومی
ip = socket.gethostbyname(socket.gethostname())
try:
    ip = subprocess.check_output(
        "curl -s ifconfig.me",
        shell=True,
        text=True
    ).strip()
except:
    pass

# ساخت سرویس systemd
service = f"""
[Unit]
Description=Telegram MTProxy
After=network.target

[Service]
WorkingDirectory=/opt/MTProxy/objs/bin
ExecStart=/opt/MTProxy/objs/bin/mtproto-proxy \
-u nobody \
-p 8888 \
-H {PORT} \
-S {secret} \
--aes-pwd proxy-secret proxy-multi.conf \
-M 1
Restart=always

[Install]
WantedBy=multi-user.target
"""

with open("/etc/systemd/system/mtproxy.service", "w") as f:
    f.write(service)

run("systemctl daemon-reload")
run("systemctl enable mtproxy")
run("systemctl restart mtproxy")

print("\n==========================")
print("MTProxy نصب شد")
print(f"IP: {ip}")
print(f"PORT: {PORT}")
print(f"SECRET: {secret}")
print("\nلینک اتصال:")
print(f"https://t.me/proxy?server={ip}&port={PORT}&secret={secret}")
print("==========================")
