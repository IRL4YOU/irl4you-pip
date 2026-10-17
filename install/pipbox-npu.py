#!/usr/bin/env python3
import json
import os
import re
import time

SRC = "/sys/kernel/debug/rknpu/load"
OUT = "/run/pipbox-npu"


def parse(text):
    cores = [int(x) for x in re.findall(r"Core\d+\s*:\s*(\d+)\s*%", text)]
    if not cores:
        m = re.search(r"(\d+)\s*%", text)
        if m:
            cores = [int(m.group(1))]
    return [max(0, min(100, c)) for c in cores][:8]


def write(cores):
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"cores": cores, "t": time.time()}, f)
    os.chmod(tmp, 0o644)
    os.replace(tmp, OUT)


def main():
    ema = None
    fails = 0
    while True:
        try:
            with open(SRC) as f:
                cores = parse(f.read())
            fails = 0
            if cores:
                ema = [float(c) for c in cores] if ema is None or len(ema) != len(cores) else [0.65 * e + 0.35 * c for e, c in zip(ema, cores)]
                write([int(round(e)) for e in ema])
            time.sleep(1)
        except OSError:
            fails += 1
            if fails >= 15:
                try:
                    os.remove(OUT)
                except OSError:
                    pass
                ema = None
            time.sleep(1)


if __name__ == "__main__":
    main()
