import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta


def next_slot(now: datetime, minute: int | None, interval: int | None) -> datetime:
    if minute is not None:
        slot = now.replace(minute=minute, second=0, microsecond=0)
        return slot if slot > now else slot + timedelta(hours=1)
    base = now.replace(second=0, microsecond=0)
    m = base.hour * 60 + base.minute
    return base + timedelta(minutes=interval - m % interval)


def main() -> None:
    args = sys.argv[1:]
    cmd = args[args.index("--") + 1 :]
    minute = int(args[args.index("--minute") + 1]) if "--minute" in args else None
    interval = int(args[args.index("--interval") + 1]) if "--interval" in args else None
    if (minute is None) == (interval is None) or not cmd:
        raise SystemExit("usage: every (--minute M | --interval N) -- command ...")
    while True:
        slot = next_slot(datetime.now(UTC), minute, interval)
        time.sleep(max(0.0, (slot - datetime.now(UTC)).total_seconds()))
        code = subprocess.run(cmd).returncode
        print(f"{datetime.now(UTC).isoformat()} {' '.join(cmd)} -> exit {code}", flush=True)


if __name__ == "__main__":
    main()
