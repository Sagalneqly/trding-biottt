"""MSS (Market Structure Shift) alert bot -> Telegram.
Pairs: XAUUSD, EURUSD, GBPUSD, GBPJPY on 5m. Runs every 5 min on GitHub Actions.
Usage:  python monitor.py          scan + send alerts
        python monitor.py --test   send a test Telegram message
        python monitor.py --scan   print recent MSS events, send nothing
"""
import os, sys, json, time
from datetime import datetime, timezone
from pathlib import Path
import requests

PAIRS = {
    "XAUUSD": ["XAUUSD=X", "GC=F"],   # tries in order
    "EURUSD": ["EURUSD=X"],
    "GBPUSD": ["GBPUSD=X"],
    "GBPJPY": ["GBPJPY=X"],
}
SWING = 3            # candles each side to confirm a swing high/low
MAX_AGE = 15 * 60    # skip if latest candle older than this (market closed)
STATE = Path(__file__).parent / "state.json"
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}"


def fetch(symbols):
    for s in symbols:
        try:
            r = requests.get(URL.format(s), params={"interval": "5m", "range": "2d"},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            ts, q = res["timestamp"], res["indicators"]["quote"][0]
            bars = []
            for i, t in enumerate(ts):
                o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
                if None in (o, h, l, c):
                    continue
                bars.append((t, o, h, l, c))
            if len(bars) > 50:
                return s, bars
        except Exception as e:
            print(f"fetch {s} failed: {e}")
    return None, []


def find_mss(bars, n=SWING):
    """Return list of (bar_index, 'BULL'|'BEAR', broken_level).
    Bullish MSS: market made a lower low, then a candle CLOSES above the swing high
    that led to that low. Bearish MSS is the mirror."""
    H = [b[2] for b in bars]; L = [b[3] for b in bars]; C = [b[4] for b in bars]
    sh, sl = [], []            # confirmed swings: (index, price)
    bull_lvl = bear_lvl = None
    events = []
    for t in range(len(bars)):
        p = t - n              # candle p becomes a confirmed pivot at time t
        if p >= n:
            if H[p] >= max(H[p-n:p]) and H[p] > max(H[p+1:p+n+1]):
                if sh and H[p] > sh[-1][1]:                # higher high
                    prev_lows = [x for x in sl if x[0] < p]
                    if prev_lows:
                        bear_lvl = prev_lows[-1][1]
                sh.append((p, H[p]))
            if L[p] <= min(L[p-n:p]) and L[p] < min(L[p+1:p+n+1]):
                if sl and L[p] < sl[-1][1]:                # lower low
                    prev_highs = [x for x in sh if x[0] < p]
                    if prev_highs:
                        bull_lvl = prev_highs[-1][1]
                sl.append((p, L[p]))
        if bull_lvl is not None and C[t] > bull_lvl:
            events.append((t, "BULL", bull_lvl)); bull_lvl = None
        if bear_lvl is not None and C[t] < bear_lvl:
            events.append((t, "BEAR", bear_lvl)); bear_lvl = None
    return events


def send(text):
    """Send via ntfy (NTFY_TOPIC) and/or Telegram (TELEGRAM_TOKEN + TELEGRAM_CHAT_ID)."""
    ok = False
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        r = requests.post(f"https://ntfy.sh/{topic}", data=text.encode("utf-8"),
                          headers={"Title": "MSS alert", "Priority": "high"}, timeout=20)
        print("ntfy:", r.status_code)
        ok = ok or r.ok
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=20)
        print("telegram:", r.status_code)
        ok = ok or r.ok
    if not topic and not (token and chat):
        print("No NTFY_TOPIC or Telegram settings found")
    return ok


def load_state():
    try: return json.loads(STATE.read_text())
    except Exception: return {}


def main():
    if "--test" in sys.argv:
        send("✅ MSS alert bot is connected."); return
    scan_only = "--scan" in sys.argv
    state, now = load_state(), time.time()
    for pair, syms in PAIRS.items():
        sym, bars = fetch(syms)
        if not bars:
            print(pair, "no data"); continue
        if bars[-1][0] + 300 > now:        # drop the still-forming candle
            bars.pop()
        if now - bars[-1][0] > MAX_AGE and not scan_only:
            print(pair, "market closed / stale"); continue
        events = find_mss(bars)
        if scan_only:
            for t, kind, lvl in events[-5:]:
                print(pair, kind, datetime.fromtimestamp(bars[t][0], timezone.utc), lvl)
            continue
        sent = state.get(pair, [])
        last = len(bars) - 1
        for t, kind, lvl in events:
            if t < last - 1:               # only the last 2 closed candles
                continue
            key = f"{bars[t][0]}-{kind}"
            if key in sent:
                continue
            icon = "🟢 BULLISH" if kind == "BULL" else "🔴 BEARISH"
            when = datetime.fromtimestamp(bars[t][0], timezone.utc).strftime("%H:%M UTC")
            msg = (f"{icon} MSS — {pair} 5m\nBroke {lvl:.5g}, closed {bars[t][4]:.5g}\n"
                   f"Candle: {when}\nWait for the pullback.")
            if send(msg):
                sent.append(key)
        state[pair] = sent[-20:]
    if not scan_only:
        STATE.write_text(json.dumps(state, indent=1))


if __name__ == "__main__":
    main()
