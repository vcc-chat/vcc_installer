import sys
import os
import io
import contextlib
import urllib3
from pathlib import Path
from subprocess import run
from getpass import getpass

devnull_file = open(os.devnull, "w")

def yes_or_no_question(prompt: str) -> bool:
    while True:
        result = input(f"{prompt} (y/N): ").lower()
        if result == "y":
            return True
        if result == "n" or not result:
            return False
        print('Please enter "y" or "n". ', end="")

def input_with_default(prompt: str, default: str):
    result = input(f"{prompt} [{default}]: ")
    return result if result else default

if sys.version_info < (3, 10):
    raise SystemExit("vcc requires at least python3.10")

if not sys.platform.startswith("linux"):
    raise SystemExit("vcc requires linux")

sys.stdout = io.TextIOWrapper(open(sys.stdout.fileno(), 'wb', 0), write_through=True)

use_ssh = yes_or_no_question("Use ssh for git clone?")

http = urllib3.PoolManager()

def pip_install(module: str):
    result = run([sys.executable, "-m", "pip", "install", module], capture_output=True)
    if result.returncode:
        raise SystemExit("pip install failed")
    
def pip_install_requirements(file: str):
    result = run([sys.executable, "-m", "pip", "install", "-r", file], capture_output=True)
    if result.returncode:
        raise SystemExit("pip install failed")

def git_clone(project):
    result = run(["git", "clone", f"git@github.com:vcc-chat/{project}.git" if use_ssh else f"https://github.com/vcc-chat/{project}.git"], capture_output=True)
    if result.returncode:
        raise SystemExit("git clone failed")
    
def get_install_path():
    default_path = Path.home() / ".vcc"
    default_path = default_path.absolute()
    return Path(input_with_default("Where do you want to install vcc?", str(default_path))).absolute()


install_path = get_install_path()
if install_path.exists():
    raise SystemExit("vcc has already been installed")
install_path.mkdir(0o700)
os.chdir(install_path)

@contextlib.contextmanager
def print_text(text: str):
    try:
        print(text, end="")
        with (
            contextlib.redirect_stdout(devnull_file),
            contextlib.redirect_stderr(devnull_file)
        ):
            yield
    except Exception as e:
        print(f" ERROR: {e=}", file=sys.stderr)
        raise SystemExit(1) from None
    else:
        print(" OK")

with print_text("Cloning vcc_rpc..."):
    git_clone("vcc_rpc")

with print_text("Installing requirements..."):
    pip_install_requirements("vcc_rpc/requirements.txt")

with print_text("Cloning web-vcc..."):
    git_clone("web-vcc")

with print_text("Installing requirements..."):
    pip_install_requirements("web-vcc/backend/requirements.txt")

with print_text("Installing supervisord..."):
    pip_install("supervisor")

is_minio_installed = yes_or_no_question("Have you installed minio and started it already?")

with print_text("Getting your public ip address..."):
    default_public_ip: str = http.request("GET", "https://checkip.amazonaws.com").data[:-1].decode()

supervisord_text = R'''
[unix_http_server]
file=./supervisor.sock

[supervisord]
environment=RPCHOST="127.0.0.1:2474",MINIO_ROOT_USER="{MINIO_USER}",MINIO_ROOT_PASSWORD="{MINIO_PASSWORD}",MINIO_URL="{IP}:9000",MINIO_ACCESS="{MINIO_USER}",MINIO_SECRET="{MINIO_PASSWORD}"
directory=%(here)s

[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix://./supervisor.sock

[program:vcc_rpc]
command={PYTHON_EXECUTABLE} ./vcc_rpc/server/main.py
autorestart=true
priority=10
startretries=3
redirect_stderr=true
stdout_logfile=./log/%(program_name)s.log

[group:services]
programs=login,chat,file,record
priority=20

[program:login]
startsecs=5
command={PYTHON_EXECUTABLE} ./vcc_rpc/services/login.py
autorestart=true
startretries=3
redirect_stderr=true
stdout_logfile=./log/service_%(program_name)s.log

[program:chat]
startsecs=5
command={PYTHON_EXECUTABLE} ./vcc_rpc/services/chat.py
autorestart=true
startretries=3
redirect_stderr=true
stdout_logfile=./log/service_%(program_name)s.log

[program:file]
startsecs=5
command={PYTHON_EXECUTABLE} ./vcc_rpc/services/file.py
autorestart=true
startretries=3
redirect_stderr=true
stdout_logfile=./log/service_%(program_name)s.log

[program:record]
startsecs=5
command={PYTHON_EXECUTABLE} ./vcc_rpc/services/record.py
autorestart=true
startretries=3
redirect_stderr=true
stdout_logfile=./log/service_%(program_name)s.log

[group:gateways]
programs=web
priority=20

[program:web]
startsecs=5
command={PYTHON_EXECUTABLE} ./web-vcc/backend/main.py
autorestart=true
startretries=3
redirect_stderr=true
stdout_logfile=./log/gateway_%(program_name)s.log
'''

supervisord_minio_text = '''
[program:minio]
command=./minio server ./data
autorestart=true
priority=10
startretries=3
redirect_stderr=true
stdout_logfile=./log/%(program_name)s.log
'''

if is_minio_installed:
    minio_username = ""
    minio_password = ""
else:
    supervisord_text += supervisord_minio_text
    minio_path = install_path / "minio"
    if not minio_path.is_file():
        architecture = input("What's your machine's architecture? (Please enter one of 'amd64', 'arm64', 'ppc64le' and 's390x'): ")
        
        with print_text("Downloading minio..."):
            minio_path.touch(0o700)
            minio_path.write_bytes(http.request("GET", f"https://dl.min.io/server/minio/release/linux-{architecture}/minio").data)
    
    minio_username = input("Enter your new username for minio: ")
    minio_password = getpass("Enter your new password for minio: ")

minio_hostname = input_with_default("What's you preferred host name for minio?", default_public_ip)

supervisord_text = supervisord_text.format_map({
    "MINIO_USER": minio_username,
    "MINIO_PASSWORD": minio_password,
    "IP": minio_hostname,
    "PYTHON_EXECUTABLE": sys.executable
})

supervisord_path = install_path / "supervisord.conf"
supervisord_path.touch(0o700)
supervisord_path.write_text(supervisord_text)

print("Installation success! Run the following commands to start the vcc server: ")
print(f"    cd {install_path}")
print("    supervisord")
print("    supervisorctl status")
