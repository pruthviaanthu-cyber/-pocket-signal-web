
import os, math
from datetime import datetime, timezone
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
TD_KEY = os.getenv("TWELVEDATA_API_KEY", "").strip()

ASSETS = {
    "EUR/USD":"EUR/USD","GBP/USD":"GBP/USD","USD/JPY":"USD/JPY",
    "AUD/USD":"AUD/USD","USD/CAD":"USD/CAD","USD/CHF":"USD/CHF",
    "BTC/USD":"BTC/USD","ETH/USD":"ETH/USD","SOL/USD":"SOL/USD",
    "XAU/USD":"XAU/USD","XAG/USD":"XAG/USD","AAPL":"AAPL","TSLA":"TSLA",
    "NVDA":"NVDA","MSFT":"MSFT","SPX":"SPX","NDX":"NDX","DAX":"DAX"
}
EXPIRIES = ["5s","10s","15s"]
ANALYSIS_INTERVAL = "1min"

def fetch_candles(symbol, interval=ANALYSIS_INTERVAL, outputsize=180):
    if not TD_KEY:
        raise RuntimeError("TWELVEDATA_API_KEY is not configured.")
    import requests
    r = requests.get(
        "https://api.twelvedata.com/time_series",
        params={"symbol":symbol,"interval":interval,"outputsize":outputsize,
                "apikey":TD_KEY,"format":"JSON"},
        timeout=15
    )
    r.raise_for_status()
    j = r.json()
    if "values" not in j:
        raise RuntimeError(j.get("message","Market-data error"))
    rows = list(reversed(j["values"]))
    if len(rows) < 80:
        raise RuntimeError("Not enough candles.")
    return rows

def ema(values, n):
    a = 2/(n+1)
    out=[values[0]]
    for x in values[1:]:
        out.append(a*x+(1-a)*out[-1])
    return out

def rsi(values, n=14):
    gains=[]; losses=[]
    for i in range(1,len(values)):
        d=values[i]-values[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    if len(gains)<n: return 50.0
    ag=sum(gains[:n])/n; al=sum(losses[:n])/n
    for i in range(n,len(gains)):
        ag=(ag*(n-1)+gains[i])/n
        al=(al*(n-1)+losses[i])/n
    return 100.0 if al==0 else 100-(100/(1+ag/al))

def macd(values, fast=12, slow=26, signal_n=9):
    ef, es = ema(values,fast), ema(values,slow)
    line=[a-b for a,b in zip(ef,es)]
    sig=ema(line,signal_n)
    return line[-1], sig[-1], line[-1]-sig[-1]

def atr(rows, n=14):
    tr=[]
    for i in range(1,len(rows)):
        h=float(rows[i]["high"]); l=float(rows[i]["low"]); pc=float(rows[i-1]["close"])
        tr.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(tr[-n:])/n

def analyze(symbol, expiry):
    rows=fetch_candles(symbol)
    close=[float(x["close"]) for x in rows]
    high=[float(x["high"]) for x in rows]
    low=[float(x["low"]) for x in rows]
    price=close[-1]
    e9,e21,e50=ema(close,9),ema(close,21),ema(close,50)
    rv=rsi(close)
    ml,ms,md=macd(close)
    a=atr(rows)
    momentum=close[-1]-close[-5]
    body=abs(close[-1]-close[-2])
    rng=max(high[-1]-low[-1],1e-12)

    # Multi-factor score. This is deliberately selective; it does not promise a win rate.
    bull=bear=0.0
    reasons=[]

    if e9[-1] > e21[-1]: bull += 18; reasons.append("EMA9 > EMA21")
    else: bear += 18; reasons.append("EMA9 < EMA21")
    if e21[-1] > e50[-1]: bull += 14
    else: bear += 14
    if price > e9[-1]: bull += 10
    else: bear += 10
    if md > 0: bull += 15
    else: bear += 15
    if 52 <= rv <= 68: bull += 12
    elif 32 <= rv <= 48: bear += 12
    elif rv > 72: bear += 8
    elif rv < 28: bull += 8

    if momentum > 0: bull += 8
    elif momentum < 0: bear += 8

    # Candle body/ATR quality filter
    if body >= 0.25*a:
        if close[-1] > close[-2]: bull += 8
        else: bear += 8

    # Avoid extremely quiet candles
    if rng >= 0.15*a:
        if close[-1] > close[-2]: bull += 5
        elif close[-1] < close[-2]: bear += 5

    total=max(bull,bear)
    direction="CALL" if bull>bear else "PUT" if bear>bull else "WAIT"
    confidence=round(min(99, 50 + abs(bull-bear)*0.72),1)

    # Strong signal requires agreement and minimum confidence.
    if confidence < 78 or abs(bull-bear) < 16:
        direction="WAIT"

    return {
        "symbol":symbol, "expiry":expiry, "price":price, "rsi":round(rv,2),
        "ema9":e9[-1], "ema21":e21[-1], "ema50":e50[-1],
        "macd_hist":md, "atr":a, "bull_score":round(bull,1),
        "bear_score":round(bear,1), "confidence":confidence,
        "signal":direction, "analysis_interval":ANALYSIS_INTERVAL,
        "time":datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "note":"5s/10s/15s are requested expiries; analysis uses 1-minute market candles."
    }

@app.get("/")
def home():
    return render_template("index.html", assets=sorted(ASSETS), expiries=EXPIRIES)

@app.get("/api/signal")
def api_signal():
    symbol=request.args.get("symbol","EUR/USD")
    expiry=request.args.get("expiry","15s")
    if symbol not in ASSETS: return jsonify({"error":"Unsupported symbol"}),400
    if expiry not in EXPIRIES: return jsonify({"error":"Unsupported expiry"}),400
    try: return jsonify(analyze(ASSETS[symbol],expiry))
    except Exception as e: return jsonify({"error":str(e)}),502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","8080")))
