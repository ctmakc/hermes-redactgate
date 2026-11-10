"""Drive a real interactive Hermes session in a pty: type like a person, wait for output.

Used to record the demo video. The terminal shows exactly what Hermes prints; this script
only types the keys. Usage: python drive.py script.json [log]
"""

import json
import os
import pty
import re
import select
import sys
import time

steps = json.load(open(sys.argv[1]))
marks_path = os.environ.get("DRIVE_MARKS")
marks = []
T0 = time.time()
log = open(sys.argv[2], "wb") if len(sys.argv) > 2 else None
cmd = steps["cmd"]
pid, fd = pty.fork()
if pid == 0:
    os.execvp(cmd[0], cmd)

buf = b""
fresh = b""


def pump(timeout):
    global buf, fresh
    end = time.time() + timeout
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if fd in r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                return False
            if not data:
                return False
            os.write(1, data)
            if log:
                log.write(data)
                log.flush()
            buf = (buf + data)[-200000:]
            fresh = (fresh + data)[-400000:]
    return True


def wait_for(pattern, timeout):
    """Wait until *pattern* shows up in output produced after this call started."""
    global fresh
    rx = re.compile(pattern.encode())
    fresh = b""
    end = time.time() + timeout
    while time.time() < end:
        pump(0.1)
        if rx.search(re.sub(rb"\x1b\[[0-9;?>]*[a-zA-Z]", b"", fresh)):
            return True
    print(f"\n[drive] timeout waiting for {pattern}", file=sys.stderr)
    return False


for st in steps["steps"]:
    if "mark" in st:
        marks.append({"mark": st["mark"], "t": time.time()})
    if "sleep" in st:
        pump(st["sleep"])
    if "wait" in st:
        wait_for(st["wait"], st.get("timeout", 60))
    if "type" in st:
        for ch in st["type"]:
            os.write(fd, ch.encode())
            pump(st.get("cps_delay", 0.03))
    if st.get("enter"):
        os.write(fd, b"\r")
pump(steps.get("tail", 2))
marks.append({"mark": "end", "t": time.time()})
if marks_path:
    json.dump({"t0": T0, "marks": marks}, open(marks_path, "w"), indent=1)
