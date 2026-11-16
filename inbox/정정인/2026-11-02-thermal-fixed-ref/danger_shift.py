"""실제 Core state 로그에서 위험(danger) 판정이 고정 기준(max ≥ 경계)으로 바뀌면 몇 초 밀리는지.
danger = 과열(temp_danger) ∧ 과한 연기(heavy, reasons SMOKE). 과한 연기는 팬 3단에서만 세므로,
그 직전 15 s에 팬 3단이 「지금 과열 경고」 때문이었고 새 기준으로는 강불 미만인 tick이 있으면 표시한다.

  python3 danger_shift.py 23.0 26.50 21.88 logs/e2e-*.state.log.gz logs/film-1027-*.state.log.gz
"""
import gzip,json,re,sys,datetime as dt
REF=float(sys.argv[1]); DANGER=float(sys.argv[2]); HIGH=float(sys.argv[3]); files=sys.argv[4:]
for f in files:
    R=[]
    for l in (gzip.open(f,"rt",encoding="utf-8",errors="replace") if f.endswith(".gz") else open(f,encoding="utf-8",errors="replace")):
        m=re.search(r"\[state\] (\{.*\})",l)
        if not m: continue
        try: p=json.loads(m.group(1))["p"]
        except: continue
        th=p["sensors"]["thermal"]
        if not th.get("valid"): continue
        R.append((float(l.split()[0]),th["max_c"],th["median_c"],p["hazard"],p["fan"]["level"],p["presence"],p["reasons"]))
    for i in range(1,len(R)):
        if R[i][3]=="danger" and R[i-1][3]!="danger":
            t0=R[i][0]
            if "TEMP_ABS" in R[i][6]: print(f,"절대고온 — 제외"); continue
            # 이 위험의 과한 연기 시작
            j=i
            while j>0 and "SMOKE" in R[j-1][6] and t0-R[j-1][0]<600: j-=1
            heavy_t=R[j][0] if "SMOKE" in R[j][6] else None
            td=next((r[0] for r in R[i::-1] if "TEMP_DANGER" not in r[6]),None)
            new=next((r for r in R[j:] if "SMOKE" in r[6] and r[1]-REF>=DANGER-1e-9 and r[0]-t0<900),None)
            win=[r for r in R if heavy_t and heavy_t-16<=r[0]<=heavy_t]
            risk=[r for r in win if "TEMP_DANGER" in r[6] and r[1]-REF<HIGH and not (r[5]!="present" and r[1]-REF>=0)]
            ts=lambda t:dt.datetime.fromtimestamp(t).strftime("%m/%d %H:%M:%S")
            peak=max(r[1] for r in R[i:i+400])
            print(f"{f.split('/')[-1]:12s} 위험 {ts(t0)} | 과한연기 시작 {ts(heavy_t) if heavy_t else '-'} | 그때 max {R[i][1]:.2f} med {R[i][2]:.2f} | "
                  f"새 기준 위험 {ts(new[0]) if new else '안 남(900 s 안)'} → 밀림 {f'{new[0]-t0:+.1f} s' if new else '-'} | 최고 {peak:.2f} | 팬3 근거 위험 {len(risk)}tick")
