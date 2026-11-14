import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def mk(t, yb, ya, e="EV", st=None, fl=None, cap=None, me=False, v24=1000, exp_in=86400 * 10, now=1_800_000_000, cs=""):
    return {"t": t, "e": e, "s": e.split("-")[0], "cat": "Test", "ti": t, "et": e,
            "yb": yb, "ya": ya, "nb": round(1 - ya, 2), "na": round(1 - yb, 2),
            "ybs": 500, "yas": 500, "lp": yb, "v24": v24, "vol": 10000, "oi": 1000,
            "close": now + exp_in, "exp": now + exp_in, "st": st, "fl": fl, "cap": cap, "me": me,
            "status": "active", "ft": "quadratic", "fm": 1.0, "result": "", "sv": None, "cs": cs}
