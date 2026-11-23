#!/usr/bin/python3
"""Update the Tschenggins Lämpli backend server (pull, firmware files, restart)

The server has a git clone of this repository with the backend running as systemd user unit. Steps:

1. Check that all local commits are pushed (--push pushes them)
2. Optional (--build): build all firmwares (all envs laempli-<type> of platformio.ini), the build copies them
   to data/fw/
3. Server: git pull --ff-only, check that the server is on the local commit
4. Copy the newest local firmware file of each name (data/fw/<name>_<version>.bin) to the server. Builds with
   uncommitted changes ("-dirty" in the version) are not used (--dirty uses them).
5. Server: remove old firmware files, keep the newest --keep files of each name
6. Server: restart the service, show its state and the log since the restart

The devices see the newest firmware file of their name when they connect. The user starts the update on the web
page (the Chlotz asks on its display).

Server settings come from the section deploy of secrets.yaml (see secrets.yaml.example): host (ssh host), dir (git
clone on the server) and optional service (systemd user unit). The command line options override them.

Needs PyYAML (Debian python3-yaml), ssh access to the server (no password prompt) and rsync on both sides.

Examples:

    tools/deploy.py                   # pull, copy firmware, restart
    tools/deploy.py --push --build    # push, build, then the same
    tools/deploy.py --no-firmware     # backend change only
"""

import argparse
import os
import re
import shlex
import subprocess
import sys

try:
    import yaml
except ImportError:
    sys.exit("yaml missing, please run: sudo apt install python3-yaml")

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_DIR, "backend"))

from laempli.firmware import FILE_RE  # noqa: E402

LOCAL_FW_DIR = os.path.join(PROJECT_DIR, "data", "fw")
FW_DIR = os.path.join(PROJECT_DIR, "fw")
PIO = os.path.join(FW_DIR, ".venv", "bin", "pio")
SECRETS_FILE = os.path.join(PROJECT_DIR, "secrets.yaml")

# Option -> default, None = required
SETTINGS = {"host": None, "dir": None, "service": "tschenggins-laempli"}

# Server side: prune, then list newest
REMOTE_PRUNE = """
import sys
from laempli.firmware import Firmware
fw = Firmware(sys.argv[1])
for file in fw.prune(int(sys.argv[2])):
    print("Removed " + file)
for name, info in sorted(fw.latest().items()):
    print("Newest {}: {}".format(name, info["version"]))
"""


class DeployError(Exception):
    """Deploy step failed"""


def step(text):
    """Print step title"""
    print("\n=== " + text, flush=True)


def run(cmd, capture=False):
    """Run command, raise on error"""
    if not capture:
        print("$ " + " ".join(shlex.quote(arg) for arg in cmd), flush=True)
    res = subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=capture, text=True)
    if res.returncode != 0:
        if capture and res.stderr:
            print(res.stderr.rstrip(), file=sys.stderr)
        raise DeployError("{} failed (exit {})".format(cmd[0], res.returncode))
    return (res.stdout.strip() if capture else "")


def remote(args, script, capture=False):
    """Run shell script on server"""
    return run(["ssh", args.host, "cd {} && {}".format(shlex.quote(args.dir), script)], capture)


def git(*cmd):
    """Run local git, return output"""
    return run(["git"] + list(cmd), capture=True)


def check_pushed(args):
    """Check or push local commits"""
    step("Local commits")
    try:
        ahead = int(git("rev-list", "--count", "@{u}..HEAD"))
    except DeployError:
        raise DeployError("Branch has no upstream")
    if ahead > 0:
        if not args.push:
            raise DeployError("{} commits not pushed, use --push".format(ahead))
        run(["git", "push"])
    print("HEAD {} is pushed".format(git("rev-parse", "--short=7", "HEAD")))


def build_all(args):
    """Build all firmwares"""
    step("Build")
    if (git("status", "--porcelain", "--untracked-files=no") != "") and (not args.dirty):
        raise DeployError("Uncommitted changes, commit or use --dirty")
    # Not only default_envs
    with open(os.path.join(FW_DIR, "platformio.ini"), encoding="utf-8") as fh:
        envs = re.findall(r"^\[env:(laempli-[a-z0-9]+)\]", fh.read(), re.MULTILINE)
    cmd = [PIO, "run", "-d", FW_DIR]
    for env in envs:
        cmd += ["-e", env]
    run(cmd)


def pull_server(args):
    """Pull on server, check commit"""
    step("Pull on " + args.host)
    remote(args, "git pull --ff-only")
    local = git("rev-parse", "HEAD")
    server = remote(args, "git rev-parse HEAD", capture=True)
    if server != local:
        print("Warning: server is on {}, local on {}".format(server[:7], local[:7]))
    dirty = remote(args, "git status --porcelain --untracked-files=no", capture=True)
    if dirty != "":
        print("Warning: server clone has changes:\n" + dirty)


def newest_firmware(args):
    """Get newest local file per name"""
    best = {}  # Name -> (time, file)
    for file in (os.listdir(LOCAL_FW_DIR) if os.path.isdir(LOCAL_FW_DIR) else []):
        match = FILE_RE.match(file)
        if (match is None) or (match["hash"].endswith("-dirty") and (not args.dirty)):
            continue
        name, stamp = match["name"], match["time"]
        if (name not in best) or (stamp > best[name][0]):
            best[name] = (stamp, file)
    return [best[name][1] for name in sorted(best)]


def copy_firmware(args):
    """Copy newest firmware files"""
    step("Firmware files")
    files = []
    for file in newest_firmware(args):
        print("Copy " + file)
        files.append(os.path.join(LOCAL_FW_DIR, file))
    if not files:
        print("No firmware files, use --build or --dirty")
    remote_fw = os.path.join(args.dir, args.fw_dir)
    remote(args, "mkdir -p " + shlex.quote(args.fw_dir))
    if files:
        run(["rsync", "--times", "--ignore-existing"] + files + ["{}:{}/".format(args.host, remote_fw)])
    remote(args, "cd backend && python3 -c {} {} {}".format(shlex.quote(REMOTE_PRUNE), shlex.quote(remote_fw),
                                                              args.keep))


def restart_service(args):
    """Restart service, show log"""
    step("Restart " + args.service)
    svc = shlex.quote(args.service)
    remote(args, ("start=$(date +%s); systemctl --user restart {0} && sleep 3; "
                  "systemctl --user is-active {0}; ok=$?; "
                  "journalctl --user -u {0} --since @$start --no-pager -o cat; exit $ok").format(svc))


def load_secrets(path):
    """Get deploy section of secrets"""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as ex:
        raise DeployError("Cannot read {}: {}".format(path, ex))
    deploy = (data.get("deploy") if isinstance(data, dict) else None) or {}
    if not isinstance(deploy, dict):
        raise DeployError("{}: deploy is not a mapping".format(path))
    return deploy


def parse_args(argv):
    """Parse command line"""
    parser = argparse.ArgumentParser(prog="deploy.py", description=__doc__.split("\n", 1)[0],
                                     epilog=__doc__.split("\n", 1)[1], formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--secrets", default=SECRETS_FILE, help="secrets file (default: %(default)s)")
    parser.add_argument("--host", help="ssh host of the server (default: deploy.host of the secrets)")
    parser.add_argument("--dir", help="git clone on the server (default: deploy.dir of the secrets)")
    parser.add_argument("--fw-dir", default="data/fw",
                        help="firmware directory on the server, relative to --dir (default: %(default)s)")
    parser.add_argument("--service", help="systemd user unit (default: deploy.service of the secrets, else {})"
                        .format(SETTINGS["service"]))
    parser.add_argument("--keep", type=int, default=3,
                        help="firmware files to keep for each name (default: %(default)s)")
    parser.add_argument("--push", action="store_true", help="push local commits first")
    parser.add_argument("--build", action="store_true", help="build all firmwares first")
    parser.add_argument("--dirty", action="store_true", help="allow builds with uncommitted changes")
    parser.add_argument("--no-firmware", action="store_true", help="do not copy or remove firmware files")
    parser.add_argument("--no-restart", action="store_true", help="do not restart the service")
    args = parser.parse_args(argv)
    if args.keep < 1:
        parser.error("--keep must be 1 or more")
    try:
        secrets = load_secrets(args.secrets)
    except DeployError as ex:
        parser.error(str(ex))
    for key, default in SETTINGS.items():
        value = getattr(args, key)
        if value is None:
            value = secrets.get(key, default)
        if (not isinstance(value, str)) or (value == ""):
            parser.error("No {0}: set deploy.{0} in secrets.yaml or use --{0}".format(key))
        setattr(args, key, value)
    return args


def main(argv=None):
    """Run all steps"""
    args = parse_args(argv)
    try:
        check_pushed(args)
        if args.build:
            build_all(args)
        pull_server(args)
        if not args.no_firmware:
            copy_firmware(args)
        if not args.no_restart:
            restart_service(args)
    except DeployError as ex:
        print("deploy: {}".format(ex), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 1
    step("Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
