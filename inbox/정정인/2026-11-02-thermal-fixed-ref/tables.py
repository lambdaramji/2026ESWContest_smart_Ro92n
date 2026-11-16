#!/usr/bin/env python3
"""근거 문서 §3 · §5 표를 logs/의 판정 CSV로 다시 만든다.

  python3 tables.py 1027   # §3: 10/27 하루 — 손난로 없는 장면 max, 지금 기준 vs 고정 기준 단계 비교, 과열이 켜지는 시각 차이
  python3 tables.py 1102   # §5: 11/2 추운 박스 — 55 °C 구간과 불씨 구간
기준: P_T_ref_c 23.0, 경계 17.46 · 21.88 · 26.50 (max 40.46 · 44.88 · 49.50 °C). 기기 profile 17.94 · 22.22 · 25.36.
"""
import csv
import datetime as dt
import glob
import gzip
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REF, NEW, OLD = 23.0, (17.46, 21.88, 26.50), (17.94, 22.22, 25.36)


def rows(pattern):
    out = []
    for f in sorted(glob.glob(os.path.join(HERE, "logs", pattern))):
        for r in csv.DictReader(gzip.open(f, "rt", newline="")):
            try:
                if r["thermal_valid"] == "1":
                    out.append((os.path.basename(f), int(r["wall_ms"]) / 1000, float(r["t_max"]),
                                float(r["t_median"]), float(r["t_metric"]), r["hazard"], r["reasons"]))
            except (ValueError, KeyError):
                pass  # 전원 차단으로 잘린 마지막 줄
    return out


def tier(v, b):
    return sum(v >= x for x in b)


def hm(t):
    return dt.datetime.fromtimestamp(t).strftime("%H:%M:%S.%f")[:10]


if sys.argv[1:] == ["1027"]:
    R = rows("judge-20261027-*.csv.gz")
    print(f"tick {len(R)}, 중앙값 {min(r[3] for r in R):.2f}–{max(r[3] for r in R):.2f} °C")
    empty = sorted(r[2] for r in R if r[4] < 9)
    print(f"손난로 없음(metric < 9) {len(empty)} tick: max 최고 {empty[-1]:.2f}, p99 {empty[int(.99 * (len(empty) - 1))]:.2f} °C")
    names = ["low", "mid", "high", "과열급"]
    same, diff = 0, {}
    for r in R:
        a, b = tier(r[4], OLD), tier(max(0.0, r[2] - REF), NEW)
        if a == b:
            same += 1
        else:
            diff[(names[a], names[b])] = diff.get((names[a], names[b]), 0) + 1
    print(f"단계 같음 {same}, 다름 {sum(diff.values())}: " + ", ".join(f"{a}→{b} {n}" for (a, b), n in sorted(diff.items())))
    for i in range(1, len(R)):
        if R[i][0] == R[i - 1][0] and R[i][4] >= OLD[2] > R[i - 1][4]:
            t0 = R[i][1]
            new = next((r[1] for r in R[i:] if r[0] == R[i][0] and r[2] - REF >= NEW[2]), None)
            dng = next((r[1] for r in R[i:] if r[0] == R[i][0] and r[5] == "danger"), None)
            print(f"{hm(t0)} 지금 과열 → 고정 기준 과열 {f'{new - t0:+.1f} s' if new else '없음'}, 실제 위험 {f'{dng - t0:+.1f} s' if dng else '없음'}")
elif sys.argv[1:] == ["1102"]:
    R = rows("judge-20261102-*.csv.gz")
    for label, a, b in [("ⓑ 1회차 55 °C", "21:15:21", "21:16:53"), ("ⓑ 2회차 55 °C", "21:31:39", "21:32:58"),
                        ("ⓑ 3회차 55 °C", "21:40:34", "21:41:37"), ("인센스 불씨", "20:27:39", "20:30:36")]:
        S = [r for r in R if a <= hm(r[1]) <= b]
        td = next((hm(r[1]) for r in S if "TEMP_DANGER" in r[6]), "없음")
        mx = max(r[2] for r in S)
        print(f"{label}: 중앙값 {min(r[3] for r in S):.2f}–{max(r[3] for r in S):.2f}, max 최고 {mx:.2f}, "
              f"지금 지표 최고 {max(r[4] for r in S):.2f}, 지금 과열 {td}, 고정 기준 {mx - REF:.2f} → {['low', 'mid', 'high', '과열'][tier(mx - REF, NEW)]}")
else:
    sys.exit(__doc__)
