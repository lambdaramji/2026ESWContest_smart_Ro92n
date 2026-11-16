#!/usr/bin/env python3
"""현장 보정 자동화 — 손난로 45·50·55·65 °C 단계 metric을 재고, 세 경계를 계산해 Pi 4 profile에 적용한다.

절차와 계산식은 deploy/README.md 「현장 보정 — 온도 경계 3개」와 같다.
  안정 = 최근 30 s metric 변동(최대 − 최소) ≤ 0.6 → 그때부터 180 s를 잰다.
  경계 = (아래 단계 metric 최대 + 위 단계 metric 최소) / 2, 소수 둘째 자리 내림. 여유 = (위 최소 − 아래 최대) / 2.
  바꾸는 키는 P_T_mid · P_T_high · P_T_danger 셋뿐. 여유 ≤ 0이면 적용하지 않는다.

  PI4=<Pi 4 주소> python3 field-cal.py               # 현장에서 재고 → 계산 → (물어본 뒤) 적용
  python3 field-cal.py --from <측정 폴더> [--trim 55=10]  # 저장된 core.log + events.csv로 다시 계산만 (적용 안 함)
  PI4=<주소> python3 field-cal.py --from <폴더> --apply   # 저장된 측정으로 계산한 값을 적용

측정 폴더(~/hoodseek-cal/YYYYMMDD-HHMM/)에는 core.log(journalctl -o short-unix 원문), events.csv(Pi 시각),
summary.md(단계 통계 · 계산 · 적용 전후 sha256)가 남는다. events.csv 형식은 t3_summary.py와 같다.
"""
import hashlib
import json
import os
import re
import select
import statistics as st
import subprocess
import sys
import threading
import time
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

STAGES = [(45, "약불"), (50, "중불"), (55, "강불"), (65, "화재위험")]
KEYS = [("P_T_mid", 45, 50), ("P_T_high", 50, 55), ("P_T_danger", 55, 65)]
STABLE_WINDOW_S = 30
STABLE_RANGE = 0.6
MEASURE_S = 180
STABLE_TIMEOUT_S = 8 * 60
MID_FLOOR = 9.36  # 빈 상태 + 손 뻗기 최대 (#64 S1) — P_T_mid는 이보다 커야 한다
PROFILE = "/data/hoodseek/profile.json"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=5", "-o", "LogLevel=ERROR"]
STATE_RE = re.compile(r"\[state\] (\{.*\})")


def floor2(x):
    return float(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_FLOOR))


def parse_line(line):
    m = STATE_RE.search(line)
    if not m:
        return None
    try:
        p = json.loads(m.group(1))["p"]
        th = p["sensors"]["thermal"]
        if not th.get("valid"):
            return None
        return {"t": float(line.split()[0]), "metric": th["metric"], "max": th["max_c"], "median": th["median_c"],
                "hazard": p["hazard"], "fan": p["fan"]["level"], "presence": p["presence"],
                "thresholds": p.get("thresholds", {})}
    except (ValueError, KeyError, IndexError):
        return None


def stage_stats(rows, a, b):
    seg = [r for r in rows if a <= r["t"] <= b]
    if not seg:
        return None
    m = [r["metric"] for r in seg]
    return {"n": len(seg), "secs": b - a, "min": min(m), "max": max(m), "mean": st.mean(m), "sd": st.pstdev(m),
            "max_c": st.mean(r["max"] for r in seg), "median_c": st.mean(r["median"] for r in seg),
            "present": sum(r["presence"] == "present" for r in seg) / len(seg),
            "fans": sorted({r["fan"] for r in seg}), "hazards": sorted({r["hazard"] for r in seg})}


def compute(stats):
    out = []
    for key, lo, hi in KEYS:
        a, b = stats[lo]["max"], stats[hi]["min"]
        out.append({"key": key, "lo": lo, "hi": hi, "lo_max": a, "hi_min": b,
                    "value": floor2((a + b) / 2), "margin": (b - a) / 2})
    return out


def problems(calc):
    bad = [f"{c['key']}: 여유 {c['margin']:.3f} ≤ 0 ({c['lo']} °C 최대 {c['lo_max']:.2f} ≥ {c['hi']} °C 최소 {c['hi_min']:.2f})"
           for c in calc if c["margin"] <= 0]
    v = [c["value"] for c in calc]
    if not (v[0] < v[1] < v[2]):
        bad.append(f"순서가 맞지 않음: {v}")
    if v[0] <= MID_FLOOR:
        bad.append(f"P_T_mid {v[0]} ≤ {MID_FLOOR} (빈 상태 + 손 뻗기 최대, #64 S1)")
    return bad


def report(stats, calc, old=None):
    lines = ["| 단계 | 측정 s | 줄 | metric 최소 | 최대 | 평균 ± sd | max °C | median °C | 재실 | 팬 | 판정 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for c, name in STAGES:
        s = stats.get(c)
        if s:
            lines.append(f"| {c} °C {name} | {s['secs']:.0f} | {s['n']} | {s['min']:.2f} | {s['max']:.2f} | "
                         f"{s['mean']:.2f} ± {s['sd']:.2f} | {s['max_c']:.2f} | {s['median_c']:.2f} | "
                         f"{s['present'] * 100:.0f} % | {s['fans']} | {', '.join(s['hazards'])} |")
    lines += ["", "| 키 | 아래 단계 최대 | 위 단계 최소 | 새 값 | 여유 | 지금 값 |", "|---|---|---|---|---|---|"]
    for c in calc:
        cur = old.get(c["key"]) if old else None
        lines.append(f"| `{c['key']}` | {c['lo']} °C {c['lo_max']:.2f} | {c['hi']} °C {c['hi_min']:.2f} | "
                     f"**{c['value']:.2f}** | {c['margin']:.3f} | {cur if cur is not None else '—'} |")
    return "\n".join(lines)


def warnings(stats):
    w = []
    for c, _ in STAGES:
        s = stats.get(c)
        if s and s["present"] < 0.9:
            w.append(f"{c} °C: 재실 {s['present'] * 100:.0f} % — 사람이 조리대 앞에 없으면 팬이 3단이 되어 값이 흔들립니다")
        if s and len(s["fans"]) > 1:
            w.append(f"{c} °C: 측정 중 팬 단수가 바뀜 {s['fans']}")
    return w


# ── 저장된 측정 다시 계산 ──
def from_folder(d, trim=None):
    trim = trim or {}
    d = Path(d).expanduser()
    rows = [r for r in (parse_line(l) for l in open(d / "core.log", encoding="utf-8", errors="replace")) if r]
    ev = [(float(a), b.strip()) for a, b in (l.split(",", 1) for l in open(d / "events.csv", encoding="utf-8")
                                              if l[:1].isdigit())]
    stats = {}
    for c, _ in STAGES:
        a = next((t for t, e in ev if e == f"안정 {c}"), None)
        b = next((t for t, e in ev if e == f"끝 {c}"), None)
        if a is not None and b is not None:
            stats[c] = stage_stats(rows, a, b - trim.get(c, 0))
    return d, rows, stats


# ── 현장 측정 ──
class Stream:
    """Pi 4 Core journal을 받아 core.log에 쓰고, [state] 줄을 rows에 쌓는다."""

    def __init__(self, host, path):
        self.rows, self.lock, self.offset = [], threading.Lock(), None
        self.f = open(path, "a", encoding="utf-8")
        self.p = subprocess.Popen(SSH + [f"root@{host}", "journalctl -u hoodseek-safety-core -f -n 0 -o short-unix"],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, errors="replace")
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        for line in self.p.stdout:
            self.f.write(line)
            self.f.flush()
            r = parse_line(line)
            if r:
                with self.lock:
                    self.rows.append(r)
                    self.offset = r["t"] - time.time()  # Pi 시각 − Mac 시각 (마지막 줄 기준)

    def pi_now(self):
        return time.time() + (self.offset or 0.0)

    def snapshot(self):
        with self.lock:
            return list(self.rows)

    def close(self):
        self.p.terminate()
        self.f.close()


def enter_pressed():
    return bool(select.select([sys.stdin], [], [], 0)[0]) and sys.stdin.readline() is not None


def measure_live(host):
    d = Path.home() / "hoodseek-cal" / time.strftime("%Y%m%d-%H%M")
    d.mkdir(parents=True, exist_ok=True)
    print(f"측정 폴더: {d}")
    s = Stream(host, d / "core.log")
    evf = open(d / "events.csv", "a", encoding="utf-8")
    if evf.tell() == 0:
        evf.write("epoch,event\n")

    def event(text):
        t = s.pi_now()
        evf.write(f"{t:.0f},{text}\n")
        evf.flush()
        return t

    t0 = time.time()
    while not s.snapshot():
        if time.time() - t0 > 15:
            sys.exit("Core state가 15 s 동안 안 옵니다. Core가 켜져 있는지, 열화상이 valid인지 보세요.")
        time.sleep(0.5)
    room = input("실내 온도(온도계, °C, 모르면 Enter): ").strip()
    event(f"조건 실내 {room or '__'}도 · 사람은 조리대 앞 · 인덕션 켬")
    stats = {}
    try:
        for c, name in STAGES:
            input(f"\n▶ {c} °C ({name}) — 손난로를 {c} °C로 맞춰 냄비 자리에 두고 Enter")
            put = event(f"넣음 {c}")
            print(f"  안정 기다리는 중 (최근 {STABLE_WINDOW_S} s 변동 ≤ {STABLE_RANGE}). 손으로 안정을 정하려면 Enter")
            stable, last_print = None, 0
            while stable is None:
                rows, now = s.snapshot(), s.pi_now()
                # Core는 판정이 바뀔 때만 state를 남기므로, 창 시작 직전 값도 창 안에서 이어진 것으로 본다
                prev = [r["metric"] for r in rows if r["t"] < now - STABLE_WINDOW_S][-1:]
                win = prev + [r["metric"] for r in rows if now - STABLE_WINDOW_S <= r["t"] <= now]
                span_ok = rows and rows[0]["t"] <= now - STABLE_WINDOW_S and now - put >= STABLE_WINDOW_S
                rng = max(win) - min(win) if win else None
                if time.time() - last_print >= 5 and rows:
                    r = rows[-1]
                    print(f"    {now - put:4.0f} s  metric {r['metric']:6.2f}  30 s 변동 {rng if rng is not None else 0:.2f}"
                          f"  {r['hazard']} · 팬 {r['fan']} · {r['presence']}")
                    last_print = time.time()
                if span_ok and win and rng <= STABLE_RANGE:
                    stable = event(f"안정 {c}")
                elif enter_pressed():
                    stable = event(f"안정 {c}")
                    print("  손으로 안정 표시")
                elif now - put > STABLE_TIMEOUT_S and time.time() - last_print >= 4.5:
                    print(f"  {STABLE_TIMEOUT_S // 60}분이 지나도 안정이 안 됩니다 — 손난로 온도·배터리를 보고, 계속하려면 Enter")
                time.sleep(0.5)
            print(f"  안정 ({stable - put:.0f} s). {MEASURE_S} s 잽니다 — 손난로·박스를 건드리지 마세요")
            while s.pi_now() < stable + MEASURE_S:
                left = stable + MEASURE_S - s.pi_now()
                if int(left) % 30 == 0:
                    r = s.snapshot()[-1]
                    print(f"    남은 {left:3.0f} s  metric {r['metric']:6.2f}  {r['hazard']} · 팬 {r['fan']} · {r['presence']}")
                time.sleep(1)
            end = event(f"끝 {c}")
            stats[c] = stage_stats(s.snapshot(), stable, end)
            x = stats[c]
            print(f"  ■ {c} °C: metric {x['min']:.2f}–{x['max']:.2f} (평균 {x['mean']:.2f}), 판정 {', '.join(x['hazards'])}")
    finally:
        s.close()
        evf.close()
    return d, stats


# ── 적용 ──
def ssh(host, cmd, inp=None):
    r = subprocess.run(SSH + [f"root@{host}", cmd], input=inp, capture_output=True, text=True,
                       stdin=None if inp is not None else subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError(f"ssh 실패 ({cmd}): {r.stderr.strip()}")
    return r.stdout


def read_profile(host):
    text = ssh(host, f"cat {PROFILE}")
    return text, json.loads(text)


def apply(host, calc, d):
    text, old = read_profile(host)
    before = hashlib.sha256(text.encode()).hexdigest()
    new = text
    for c in calc:
        new, n = re.subn(rf'("{c["key"]}"\s*:\s*)-?[0-9.]+', rf'\g<1>{c["value"]:.2f}', new)
        if n != 1:
            raise RuntimeError(f"{c['key']} 를 profile에서 한 번만 찾지 못함 ({n}번)")
    nj = json.loads(new)
    changed = sorted(k for k in set(old) | set(nj) if old.get(k) != nj.get(k))
    if not set(changed) <= {c["key"] for c in calc}:
        raise RuntimeError(f"세 키 말고 다른 키가 바뀜: {changed}")
    after = hashlib.sha256(new.encode()).hexdigest()
    bak = f"{PROFILE}.bak-{before[:8]}"
    ssh(host, f"cp {PROFILE} {bak}")
    ssh(host, f"cat > {PROFILE}.new && mv {PROFILE}.new {PROFILE}", inp=new)
    got = ssh(host, f"sha256sum {PROFILE}").split()[0]
    if got != after:
        raise RuntimeError(f"올린 파일 해시가 다름: {got} (기대 {after}) — {bak}로 되돌리세요")
    since = ssh(host, "date +%s").strip()
    ssh(host, "systemctl kill -s HUP hoodseek-safety-core")
    want = {"t_mid": calc[0]["value"], "t_high": calc[1]["value"], "t_danger": calc[2]["value"]}
    ok, log = False, ""
    for _ in range(10):
        time.sleep(1)
        log = ssh(host, f"journalctl -u hoodseek-safety-core --since @{since} --no-pager")
        if "profile 재로드 실패" in log:
            break
        th = [r["thresholds"] for r in (parse_line(l) for l in log.splitlines()) if r]
        if "SIGHUP: profile 재로드" in log and th and all(abs(th[-1].get(k, -1) - v) < 1e-9 for k, v in want.items()):
            ok = True
            break
    result = (f"- 적용 {time.strftime('%F %T')}: profile sha256 `{before[:16]}` → `{after[:16]}`, 백업 `{bak}`\n"
              f"- 바뀐 키: {', '.join(f'{k} {old[k]} → {nj[k]}' for k in changed) or '없음(값이 같음)'}\n"
              f"- Core 재로드: {'확인 (state thresholds가 새 값)' if ok else '확인 못 함 — 로그를 보세요'}\n"
              f"- 되돌리기: `ssh root@$PI4 'cp {bak} {PROFILE} && systemctl kill -s HUP hoodseek-safety-core'`\n")
    print("\n" + result)
    with open(d / "summary.md", "a", encoding="utf-8") as f:
        f.write("\n## 적용\n\n" + result)
    if not ok:
        print(log[-1500:])
        sys.exit(1)


def main():
    args = sys.argv[1:]
    host = os.environ.get("PI4")
    if "--from" in args:
        trim = {}
        if "--trim" in args:  # t3_summary.py와 같음: --trim 55=10 → 「끝 55」 10 s 전까지만
            k, v = args[args.index("--trim") + 1].split("=")
            trim[int(k)] = float(v)
        d, rows, stats = from_folder(args[args.index("--from") + 1], trim)
    else:
        if not host:
            sys.exit("PI4 주소를 넣어 주세요: PI4=<주소> python3 field-cal.py")
        d, stats = measure_live(host)
    missing = [c for c, _ in STAGES if not stats.get(c)]
    if missing:
        sys.exit(f"측정이 없는 단계: {missing}")
    calc = compute(stats)
    old = None
    if host:
        try:
            old = read_profile(host)[1]
        except Exception as e:  # 접속이 안 돼도 계산 결과는 보여 준다
            print(f"(지금 profile을 못 읽음: {e})")
    text = report(stats, calc, old)
    bad, warn = problems(calc), warnings(stats)
    print("\n" + text)
    for w in warn:
        print("주의:", w)
    for b in bad:
        print("적용 불가:", b)
    with open(d / "summary.md", "w", encoding="utf-8") as f:
        f.write(f"# 현장 보정 {time.strftime('%F %T')}\n\n[실측] `field-cal.py`, 폴더 `{d}`\n\n{text}\n")
        for w in warn:
            f.write(f"\n- 주의: {w}")
        for b in bad:
            f.write(f"\n- 적용 불가: {b}")
        f.write("\n")
    print(f"\n기록: {d / 'summary.md'}")
    if bad:
        print("여유가 0 이하이거나 순서가 틀려 적용하지 않습니다. 기존 값을 그대로 두고 PM에게 알리세요.")
        sys.exit(2)
    if not host or ("--from" in args and "--apply" not in args):
        return
    if input("\nPi 4 profile에 세 값을 적용할까요? (y/N) ").strip().lower() == "y":
        apply(host, calc, d)
    else:
        print("적용하지 않았습니다. 나중에: PI4=<주소> python3 field-cal.py --from", d, "--apply")


if __name__ == "__main__":
    main()
