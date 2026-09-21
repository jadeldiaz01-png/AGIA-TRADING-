from __future__ import annotations
import argparse, hashlib, io, itertools, json, math, urllib.request, zipfile
from dataclasses import dataclass, asdict, replace
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.stats import norm

PER_YEAR=1460
DOCUMENTED_HALTS={
 "2018-02-09T06:00:00+00:00":"https://www.binance.com/en/support/announcement/detail/360000737572",
 "2018-06-26T12:00:00+00:00":"https://www.binance.com/en/support/announcement/detail/360005098352",
 "2019-05-15T12:00:00+00:00":"https://www.binance.com/en/support/announcement/detail/360028054052",
}
COLS=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]

@dataclass(frozen=True)
class Config:
    short:int=40; medium:int=120; long:int=360; vol:int=80
    target_vol:float=.15; high_vol:float=.80; high_vol_mult:float=.35
    sideways_mult:float=.25; short_mult:float=0.0; min_delta:float=.05
    kill_dd:float=.20
    def hash(self):
        return hashlib.sha256(json.dumps(asdict(self),sort_keys=True,separators=(",",":")).encode()).hexdigest()

def months(start,end):
    y,m=map(int,start.split("-")); ey,em=map(int,end.split("-"))
    while (y,m)<=(ey,em):
        yield y,m
        m+=1
        if m==13:y,m=y+1,1

def get(url):
    req=urllib.request.Request(url,headers={"User-Agent":"AGIA-IQROS/0.5 certification"})
    with urllib.request.urlopen(req,timeout=60) as r:return r.read()

def epoch(v):
    v=int(v); return pd.to_datetime(v,unit="us" if abs(v)>=10**15 else "ms",utc=True)

def download(symbol,start,end,cache):
    frames=[]; provenance=[]
    for y,m in months(start,end):
        ym=f"{y:04d}-{m:02d}"; name=f"{symbol}-6h-{ym}.zip"
        base=f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/6h/{name}"
        zpath=cache/name; cpath=cache/(name+".CHECKSUM")
        if not zpath.exists(): zpath.write_bytes(get(base))
        if not cpath.exists(): cpath.write_bytes(get(base+".CHECKSUM"))
        z=zpath.read_bytes(); expected=cpath.read_text().strip().split()[0]; actual=hashlib.sha256(z).hexdigest()
        if actual.lower()!=expected.lower(): raise RuntimeError(f"CHECKSUM_FAIL {name}")
        with zipfile.ZipFile(io.BytesIO(z)) as f:
            names=[x for x in f.namelist() if x.endswith(".csv")]
            if len(names)!=1: raise RuntimeError(f"ARCHIVE_SHAPE_FAIL {name}")
            raw=f.read(names[0])
        df=pd.read_csv(io.BytesIO(raw),header=None,names=COLS)
        frames.append(df); provenance.append({"file":name,"sha256":actual,"source":base})
    d=pd.concat(frames,ignore_index=True)
    for c in ["open","high","low","close","volume"]:d[c]=pd.to_numeric(d[c],errors="raise")
    d["open_time"]=d["open_time"].map(epoch); d=d.set_index("open_time").sort_index()
    duplicates=int(d.index.duplicated().sum()); d=d[~d.index.duplicated(keep="last")]
    invalid=int(((d.high<d[["open","close","low"]].max(axis=1))|(d.low>d[["open","close","high"]].min(axis=1))|(d.low<=0)).sum())
    diffs=d.index.to_series().diff().dropna(); gap_mask=diffs!=pd.Timedelta(hours=6); gaps=int(gap_mask.sum()); gap_examples=[{"at":x.isoformat(),"delta":str(diffs.loc[x])} for x in diffs.index[gap_mask][:20]]
    documented=[{"at":x["at"],"source":DOCUMENTED_HALTS[x["at"]]} for x in gap_examples if x["at"] in DOCUMENTED_HALTS]
    undocumented=[x for x in gap_examples if x["at"] not in DOCUMENTED_HALTS]
    quality={"rows":len(d),"duplicates":duplicates,"invalid_ohlc":invalid,"gaps":gaps,"documented_exchange_halts":documented,"undocumented_gaps":undocumented,"start":d.index[0].isoformat(),"end":d.index[-1].isoformat(),"gap_examples":gap_examples}
    quality["valid"]=duplicates==0 and invalid==0 and len(undocumented)==0
    digest=hashlib.sha256(pd.util.hash_pandas_object(d,index=True).values.tobytes()).hexdigest()
    return d,{"symbol":symbol,"dataset_sha256":digest,"quality":quality,"archives":provenance}

def targets(d,cfg):
    close=d.close.astype(float)
    moms=[close/close.shift(x)-1 for x in [cfg.short,cfg.medium,cfg.long]]
    vote=sum(np.sign(x) for x in moms)/3
    sig=(vote>=2/3).astype(float) # SPOT certification is explicitly long/flat
    vol=close.pct_change().rolling(cfg.vol).std()*math.sqrt(PER_YEAR)
    scale=(cfg.target_vol/vol).clip(upper=1.0).replace([np.inf,-np.inf],np.nan).fillna(0)
    regime=np.where(vol>=cfg.high_vol,"HIGH_VOL",np.where(sig>0,"BULL_TREND","SIDEWAYS"))
    mult=np.where(regime=="HIGH_VOL",cfg.high_vol_mult,np.where(regime=="SIDEWAYS",cfg.sideways_mult,1.0))
    return pd.Series((sig*scale*mult).clip(0,1),index=d.index),pd.Series(regime,index=d.index)

def simulate(d,cfg,cost_bps=10,start_score=0):
    t,reg=targets(d,cfg); opens=d.open.to_numpy(float)
    equity=1000.0; peak=1000.0; pos=0.0; killed=False
    eq=[]; rets=[]; costs=0.0; trade_pnls=[]; last_trade_eq=equity
    regime_pnl={k:0.0 for k in ["BULL_TREND","SIDEWAYS","HIGH_VOL"]}
    for i in range(1,len(d)):
        desired=float(t.iloc[i-1]) if i>=start_score and not killed else 0.0
        if abs(desired-pos)>=cfg.min_delta:
            c=equity*abs(desired-pos)*cost_bps/10000; equity-=c; costs+=c
            if pos>0 and desired==0: trade_pnls.append(equity-last_trade_eq); last_trade_eq=equity
            pos=desired
        r=pos*(opens[i]/opens[i-1]-1)
        pnl=equity*r; equity+=pnl
        if i>=start_score: regime_pnl[str(reg.iloc[i-1])]+=pnl
        peak=max(peak,equity)
        if peak>0 and (peak-equity)/peak>=cfg.kill_dd: killed=True
        eq.append(equity); rets.append(r)
    if pos>0:
        c=equity*pos*cost_bps/10000; equity-=c; costs+=c; trade_pnls.append(equity-last_trade_eq)
    scored=np.asarray(eq[max(0,start_score-1):],float)
    rr=np.diff(scored)/scored[:-1] if len(scored)>1 else np.array([])
    ann_ret=(scored[-1]/scored[0])**(PER_YEAR/max(1,len(rr)))-1 if len(scored)>1 and scored[-1]>0 else 0
    sharpe=float(rr.mean()/rr.std(ddof=1)*math.sqrt(PER_YEAR)) if len(rr)>1 and rr.std(ddof=1)>0 else 0
    peaks=np.maximum.accumulate(scored) if len(scored) else np.array([1000.])
    dd=float(np.max((peaks-scored)/peaks)) if len(scored) else 0
    return {"net_pnl":float(equity-1000),"annualized_return":float(ann_ret),"sharpe":sharpe,"max_drawdown":dd,"costs":costs,"killed":killed,"returns":rr,"trade_pnls":trade_pnls,"regime_pnl":regime_pnl}

def configs():
    base=Config()
    for s,m,l,v,hvm in itertools.product([30,40],[90,120],[300,360],[.10,.15],[.25,.35]):
        if s<m<l: yield replace(base,short=s,medium=m,long=l,target_vol=v,high_vol_mult=hvm)

def public(r):
    return {k:v for k,v in r.items() if k not in {"returns","trade_pnls"}}

def dsr_probability(returns,trials):
    r=np.asarray(returns,float); n=len(r)
    if n<10 or np.std(r,ddof=1)==0:return 0.0
    sr=float(r.mean()/r.std(ddof=1)); skew=float(pd.Series(r).skew()); kurt=float(pd.Series(r).kurt()+3)
    sr_std=math.sqrt(max(1e-12,(1-skew*sr+((kurt-1)/4)*sr*sr)/(n-1)))
    gamma=.5772156649; N=max(1,trials)
    sr0=sr_std*((1-gamma)*norm.ppf(1-1/N)+gamma*norm.ppf(1-1/(N*math.e))) if N>1 else 0
    z=(sr-sr0)*math.sqrt(n-1)/math.sqrt(max(1e-12,1-skew*sr+((kurt-1)/4)*sr*sr))
    return float(norm.cdf(z))

def pbo_cscv(pre_oos,cs,blocks=8):
    # CSCV is performed on causal full-history strategy returns; never concatenate
    # raw non-contiguous price blocks, which would create artificial boundary returns.
    matrix=[]
    for cfg in cs:
        r=np.asarray(simulate(pre_oos,cfg)["returns"],float)
        matrix.append(r)
    min_n=min(len(x) for x in matrix); matrix=np.asarray([x[-min_n:] for x in matrix])
    idx=np.array_split(np.arange(min_n),blocks); logits=[]
    def sr(x):
        x=np.asarray(x,float); s=x.std(ddof=1)
        return float(x.mean()/s) if len(x)>1 and s>0 else 0.0
    for train_blocks in itertools.combinations(range(blocks),blocks//2):
        if 0 not in train_blocks: continue
        test_blocks=[i for i in range(blocks) if i not in train_blocks]
        tr=np.concatenate([idx[i] for i in train_blocks]); te=np.concatenate([idx[i] for i in test_blocks])
        train_scores=[sr(x[tr]) for x in matrix]; test_scores=[sr(x[te]) for x in matrix]
        winner=int(np.argmax(train_scores))
        rank=(np.argsort(np.argsort(test_scores))[winner]+1)/(len(cs)+1)
        logits.append(math.log(rank/(1-rank)))
    return float(np.mean(np.asarray(logits)<=0)) if logits else 1.0

def bootstrap_mc(trades,seed=20260920,n=5000):
    a=np.asarray(trades,float)
    if len(a)<3:return {"bootstrap_positive_probability":0.0,"ruin_probability_30pct":1.0}
    rng=np.random.default_rng(seed); pos=0; ruin=0
    for _ in range(n):
        sample=rng.choice(a,len(a),replace=True)
        if sample.sum()>0:pos+=1
        path=1000+np.cumsum(rng.permutation(a)); curve=np.r_[1000,path]; peaks=np.maximum.accumulate(curve)
        if np.max((peaks-curve)/peaks)>=.30:ruin+=1
    return {"bootstrap_positive_probability":pos/n,"ruin_probability_30pct":ruin/n}

def validate(d,symbol):
    n=len(d); a=int(n*.60); b=int(n*.80); train=d.iloc[:a]; val=d.iloc[a:b]; oos=d.iloc[b:]
    cs=list(configs()); train_results=[simulate(train,c) for c in cs]
    winner=int(np.argmax([x["sharpe"]+.5*x["annualized_return"]-1.5*x["max_drawdown"] for x in train_results]))
    c=cs[winner]; vr=simulate(val,c); o=simulate(oos,c)
    stress={str(x):public(simulate(oos,c,x)) for x in [5,10,15,20,30,50]}
    pert=[]
    for f in [.8,.9,1.1,1.2]:
        s=max(5,int(c.short*f)); m=max(s+1,int(c.medium*f)); l=max(m+1,int(c.long*f))
        pert.append(public(simulate(oos,replace(c,short=s,medium=m,long=l))))
    wf=[]; warm=max(c.long+5,int(n*.5)); step=(n-warm)//4
    for k in range(4):
        st=warm+k*step; en=n if k==3 else st+step; ctx=max(0,st-(c.long+5))
        wf.append(public(simulate(d.iloc[ctx:en],c,10,st-ctx)))
    pre=d.iloc[:b]; pbo=pbo_cscv(pre,cs); dsr=dsr_probability(o["returns"],len(cs)); bm=bootstrap_mc(o["trade_pnls"])
    regimes={k:float(v) for k,v in o["regime_pnl"].items()}
    gates={
      "VALIDATION_NET_POSITIVE":vr["net_pnl"]>0,
      "OOS_NET_POSITIVE":o["net_pnl"]>0 and o["sharpe"]>0 and not o["killed"],
      "WALK_FORWARD":sum(x["net_pnl"]>0 for x in wf)>=3,
      "COST_STRESS_20BPS":stress["20"]["net_pnl"]>0,
      "PARAMETER_STABILITY":sum(x["net_pnl"]>0 for x in pert)>=3,
      "DSR":dsr>=.95,
      "PBO":pbo<=.25,
      "BOOTSTRAP":bm["bootstrap_positive_probability"]>=.90,
      "RUIN":bm["ruin_probability_30pct"]<=.05,
      "REGIME_NONCONCENTRATION":sum(v>0 for v in regimes.values())>=2
    }
    return {"symbol":symbol,"config":asdict(c),"config_hash":c.hash(),"train_best":public(train_results[winner]),"validation":public(vr),"oos":public(o),"cost_stress":stress,"walk_forward":wf,"parameter_perturbation":pert,"dsr_probability":dsr,"pbo":pbo,**bm,"regime_pnl":regimes,"gates":gates,"verdict":"PASS" if all(gates.values()) else "FAIL"}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--start",default="2018-01"); p.add_argument("--end",default="2026-08"); p.add_argument("--out",default="evidence/iqros-atvf-real-market-manifest.json"); p.add_argument("--cache",default=".cache/binance"); a=p.parse_args()
    cache=Path(a.cache); cache.mkdir(parents=True,exist_ok=True); datasets=[]; results=[]
    for sym in ["BTCUSDT","ETHUSDT"]:
        d,meta=download(sym,a.start,a.end,cache)
        print(json.dumps({"symbol":sym,"quality":meta["quality"]},indent=2));
        if not meta["quality"]["valid"]: raise SystemExit(f"{sym} DATA_QUALITY_FAIL")
        datasets.append(meta); results.append(validate(d,sym))
    aggregate="PASS" if all(x["verdict"]=="PASS" for x in results) else "FAIL"
    doc={"schema":"iqros.atvf.real-evidence.v2","strategy":"IQROS_ATVF_v1_SPOT_LONG_FLAT","market":"BINANCE_SPOT","timeframe":"6h","periods_per_year":1460,"preregistered_window":{"start":a.start,"end":a.end},"selection":"TRAIN_ONLY_60pct","validation":"20pct","oos":"FINAL_20pct_FROZEN","trial_budget":32,"execution":"signal_close_t_to_open_t+1","shorting":"DISABLED_FOR_SPOT","datasets":datasets,"results":results,"aggregate_verdict":aggregate,"historical_evidence_pass":aggregate=="PASS","forward_paper_required":aggregate=="PASS","real_money_authorized":False}
    canonical=json.dumps(doc,sort_keys=True,separators=(",",":"),default=str).encode(); doc["manifest_sha256"]=hashlib.sha256(canonical).hexdigest()
    out=Path(a.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(doc,indent=2,sort_keys=True,default=str)+"\n")
    print(json.dumps({"aggregate_verdict":aggregate,"manifest":str(out),"manifest_sha256":doc["manifest_sha256"],"results":[{"symbol":x["symbol"],"verdict":x["verdict"],"gates":x["gates"]} for x in results]},indent=2))
if __name__=="__main__":main()
