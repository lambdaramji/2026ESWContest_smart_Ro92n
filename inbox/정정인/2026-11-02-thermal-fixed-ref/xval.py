#!/usr/bin/env python3
"""손난로 단계 교차 검증: 한 날의 로그로 세 경계를 잡고 다른 날 로그를 몇 샘플 맞히는지 (지금 metric vs 고정 기준).

  python3 xval.py <폴더> [기준 °C, 기본 23.0]   # 폴더 안에 T3_1024 · T3_1025 · T3_1025b (core.log + events.csv)
재현 절차는 같은 폴더의 README.md.
"""
import json,re,sys,math
from pathlib import Path
S=Path(sys.argv[1]); REF=float(sys.argv[2]) if len(sys.argv) > 2 else 23.0
def load(run):
    d=S/run; R=[]
    for l in open(d/"core.log",encoding="utf-8",errors="replace"):
        m=re.search(r"\[state\] (\{.*\})",l)
        if m:
            th=json.loads(m.group(1))["p"]["sensors"]["thermal"]
            if th.get("valid"): R.append((float(l.split()[0]),th["max_c"],th["median_c"]))
    E=[(float(a),b.strip()) for a,b in (x.split(",",1) for x in open(d/"events.csv",encoding="utf-8") if x[:1].isdigit())]
    out={}
    for i,(t,e) in enumerate(E):
        m=re.fullmatch(r"안정 (\d\d)(팬3)?",e)
        if not m: continue
        tag=m.group(1)+(m.group(2) or ""); end=next((a for a,b in E[i+1:] if b=="끝 "+tag),None)
        if end is None: continue
        if run=="T3_1024" and tag=="55": end-=10
        out.setdefault(int(m.group(1)),[]).extend([r for r in R if t<=r[0]<=end])
    return out
day={"10/24":[load("T3_1024")],"10/25":[load("T3_1025"),load("T3_1025b")]}
def merge(ls):
    o={}
    for x in ls:
        for k,v in x.items(): o.setdefault(k,[]).extend(v)
    return o
D={k:merge(v) for k,v in day.items()}
F={"지금 방식 max−median":lambda r:r[1]-r[2], f"고정 기준 max−{REF}":lambda r:max(0,r[1]-REF)}
def bounds(data,f):
    b=[]
    for lo,hi in [(45,50),(50,55),(55,65)]:
        if lo not in data or hi not in data: b.append(None); continue
        a=max(f(r) for r in data[lo]); c=min(f(r) for r in data[hi]); b.append(math.floor((a+c)/2*100)/100)
    return b
def tier(v,b):
    return sum(v>=x for x in b)
want={45:0,50:1,55:2,65:3}
for name,f in F.items():
    print(f"[{name}]")
    for train,test in [("10/24","10/25"),("10/25","10/24")]:
        b=bounds(D[train],f)
        if None in b: print(f"  {train}로 경계: 단계 부족 {b}"); continue
        res=[]
        for c,rows in sorted(D[test].items()):
            ok=sum(tier(f(r),b)==want[c] for r in rows); res.append(f"{c}°C {ok}/{len(rows)}")
        print(f"  {train} 경계 {b} → {test} 맞힘: "+", ".join(res))
