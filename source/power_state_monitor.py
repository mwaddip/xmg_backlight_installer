#!/usr/bin/env python3
"""Monitor AC/battery transitions and reapply keyboard profile."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from typing import List, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESTORE_SCRIPT = os.path.join(BASE_DIR, "restore_profile.py")
PYTHON_EXECUTABLE = sys.executable or shutil.which("python3") or "/usr/bin/python3"
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "backlight-linux")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")
PROFILE_PATH = os.path.join(CONFIG_DIR, "profile.json")
POWER_SUPPLY_DIR = "/sys/class/power_supply"
MAINS_TYPES = {"mains", "ac", "usb"}
POLL_INTERVAL_SECONDS = 3
REDISCOVER_INTERVAL = 20  # iterations


def log(msg: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)


def discover_mains_online_paths() -> List[str]:
    if not os.path.isdir(POWER_SUPPLY_DIR):
        return []
    paths: List[str] = []
    for entry in os.listdir(POWER_SUPPLY_DIR):
        entry_path = os.path.join(POWER_SUPPLY_DIR, entry)
        type_path = os.path.join(entry_path, "type")
        online_path = os.path.join(entry_path, "online")
        try:
            with open(type_path, "r", encoding="utf-8") as handle:
                power_type = handle.read().strip().lower()
        except OSError:
            continue
        if power_type not in MAINS_TYPES:
            continue
        if os.path.isfile(online_path):
            paths.append(online_path)
    return sorted(set(paths))


def read_online_value(path: str) -> Optional[bool]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip() == "1"
    except OSError:
        return None


def ensure_restore_script_executable() -> None:
    try:
        st = os.stat(RESTORE_SCRIPT)
    except FileNotFoundError:
        return
    new_mode = st.st_mode | 0o111
    if new_mode != st.st_mode:
        try:
            os.chmod(RESTORE_SCRIPT, new_mode)
        except OSError:
            pass


def read_settings() -> dict:
    """Read settings.json to get AC/battery profile preferences."""
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def read_profile_store() -> dict:
    """Read profile.json to get available profiles."""
    try:
        with open(PROFILE_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def switch_active_profile(profile_name: str) -> bool:
    """Switch the active profile in profile.json."""
    store = read_profile_store()
    if not store or "profiles" not in store:
        return False
    if profile_name not in store.get("profiles", {}):
        log(f"Profile '{profile_name}' not found.")
        return False
    store["active"] = profile_name
    try:
        tmp_path = PROFILE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(store, handle, indent=2)
        os.replace(tmp_path, PROFILE_PATH)
        return True
    except OSError as exc:
        log(f"Failed to save profile: {exc}")
        return False


def restore_profile(reason: str, power_state: Optional[bool] = None) -> None:
    ensure_restore_script_executable()
    if not os.path.isfile(RESTORE_SCRIPT):
        log(f"restore_profile.py not found ({RESTORE_SCRIPT}).")
        return

    # Check if we should switch to a power-specific profile
    if power_state is not None:
        settings = read_settings()
        target_profile = ""
        if power_state:  # On AC
            target_profile = settings.get("ac_profile", "")
        else:  # On battery
            target_profile = settings.get("battery_profile", "")
        
        if target_profile:
            log(f"Switching to {'AC' if power_state else 'battery'} profile: {target_profile}")
            if switch_active_profile(target_profile):
                log(f"Active profile switched to '{target_profile}'")
            else:
                log(f"Cannot switch profile to '{target_profile}', keeping current profile")

    cmd = [PYTHON_EXECUTABLE, RESTORE_SCRIPT]
    log(f"{reason}: running {' '.join(shlex.quote(part) for part in cmd)}")
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.stdout:
        print(proc.stdout.strip(), flush=True)
    if proc.stderr:
        print(proc.stderr.strip(), file=sys.stderr, flush=True)
    if proc.returncode != 0:
        log(f"restore_profile.py exited with code {proc.returncode}")


def compute_power_state(paths: List[str]) -> Optional[bool]:
    if not paths:
        return None
    any_online = False
    any_offline = False
    for path in paths:
        value = read_online_value(path)
        if value is True:
            any_online = True
            break
        if value is False:
            any_offline = True
    if any_online:
        return True
    if any_offline:
        return False
    return None


def monitor_loop() -> int:
    iteration = 0
    paths = discover_mains_online_paths()
    last_state = compute_power_state(paths)
    if last_state is None:
        log("Unable to determine initial power state.")
    else:
        log(f"Initial power state: {'AC' if last_state else 'battery'}")
        restore_profile("Initial power state", power_state=last_state)

    while True:
        iteration += 1
        if iteration % REDISCOVER_INTERVAL == 0:
            paths = discover_mains_online_paths()

        state = compute_power_state(paths)
        if state is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        if last_state is None:
            last_state = state
        elif state != last_state:
            label = "AC" if state else "battery"
            log(f"Power source changed: now on {label}.")
            restore_profile("Power source change", power_state=state)
            last_state = state

        time.sleep(POLL_INTERVAL_SECONDS)


def main() -> int:
    try:
        monitor_loop()
    except KeyboardInterrupt:
        log("Monitor interrupted.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
