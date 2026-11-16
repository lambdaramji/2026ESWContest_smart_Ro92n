# 리허설 Core 로그(정인이 받는 safetycore journal)에서 #215 §6 기록표 시각을 뽑는다. 기기 없이 PC에서만.
# 사용: python3 -I inbox/김병직/tools/rehearsal_times.py <core.log>...   (시험: rehearsal_times_test.py)
#  로그는 `journalctl -u hoodseek-safety-core -o short-unix`(줄 앞 숫자 = 시각) 그대로. [state]·[tcp] 줄만 본다.
#  1) 사건 시각표: 인덕션·팬·음성·hazard·lock·presence·vision cat·주요 reasons가 바뀐 때(PM2.5·metric 함께)
#  2) 요약: ⓐ high 시작, L4 locked_cat 구간(그동안 cat·PM2.5), ⓑ·ⓒ danger 뒤 열·SMOKE·ACK_WAIT·① 0·해제,
#     (① 0 = state `lock_release_left_s`가 처음 0이 된 때 — Core가 센 값이라 P_lock_release_s·다시 세기와 맞음)
#     Pi 5 연결 끊김(`[tcp] … 끊음`) 줄 수와 시각 — vision이 2 s 넘게 끊기면 presence가 unknown이 돼 L4가 흔들린다
#     (ui는 vision·voice와 같은 주소면 Pi 5 앱, 아니면 다른 기기 — 리허설 중 Mac 앱 끊김을 가른다)
#     vision 2 s 만료(`vision.fresh` true→false) 횟수 — 연결이 살아 있어도 Wi-Fi가 2 s 가까이 멈추면 생긴다
# 시작 시각(손난로 투입·65 °C 전환·인센스 뺌)은 로그에 없으니 스톱워치 시각과 빼서 쓴다.
import json, re, sys, time

# HOLD·TEMP_HIGH는 매초 깜빡여 뺌 — 단계 변화는 hazard로 보임
KEY_REASONS = ("TEMP_DANGER", "SMOKE", "PM_WARN", "CAT_NEAR", "ACK_WAIT", "UNLOCK_WAIT", "ABSENT")
hms = lambda t: time.strftime("%H:%M:%S", time.localtime(t))


def view(p):
    return {
        "ind": p["induction"]["level"], "fan": p["fan"]["level"], "voice": (p.get("voice") or {}).get("last_intent"),
        "hazard": p["hazard"], "lock": p["lock"], "presence": p["presence"], "cat": p["vision"]["cat"],
        "reasons": tuple(r for r in p["reasons"] if r in KEY_REASONS),
    }


for path in sys.argv[1:]:
    print("==", path.split("/")[-1])
    prev = None; t0 = None
    highs, cats, dangers = [], [], []
    cat_ep = None; d = None
    kinds = {}; drops = []  # [tcp] 접속 주소 → hello 종류(ui·vision·voice), 끊음 (시각, 종류)
    # vision·voice가 붙은 주소(포트 뺌) — 그 둘은 Pi 5에서만 돈다. 끊김 뒤에 다시 붙는 hello도 쓰려고 먼저 한 번 훑는다
    pi5 = {m.group(1).rsplit(":", 1)[0] for m in re.finditer(r"\[tcp\] (\S+) hello \((?:vision|voice)\)", open(path, errors="replace").read())}
    fresh = None; expires = []
    for line in open(path, errors="replace"):
        if "[tcp] " in line:
            m = re.search(r"\[tcp\] (\S+) (hello \((\w+)\)|끊음)", line)
            if m and m.group(3): kinds[m.group(1)] = m.group(3)
            # 로그 시작 전에 붙은 연결은 종류를 모른다. 주소는 댓글·저장소에 남기지 않는다(CLAUDE.md 환경값)
            elif m:
                k = kinds.get(m.group(1), "앞선 접속")
                if k == "ui": k = "ui(Pi 5)" if m.group(1).rsplit(":", 1)[0] in pi5 else "ui(다른 기기)"
                drops.append((float(line.split()[0]), k))
            continue
        if "[state] " not in line: continue
        t = float(line.split()[0]); p = json.loads(line.split("[state] ", 1)[1])["p"]
        v = view(p); pm = p["sensors"]["pm"].get("pm25") or 0; metric = p["sensors"]["thermal"].get("metric") or 0
        if t0 is None and v["ind"] > 0: t0 = t
        if fresh and not p["vision"]["fresh"]: expires.append(t)
        fresh = p["vision"]["fresh"]
        if prev and v != prev:
            diff = " ".join(f"{k}={prev[k]}→{v[k]}" for k in v if v[k] != prev[k])
            rel = f"+{t - t0:6.1f}" if t0 else "      -"
            print(f"  {hms(t)} {rel} s  {diff}  (pm25={pm:.0f} metric={metric:.1f})")
        # ⓐ: low·mid → high
        if prev and v["hazard"] == "high" and prev["hazard"] in ("low", "mid"): highs.append(t)
        # L4: locked_cat 구간과 그동안 cat true 비율·PM2.5 최고
        if v["lock"] == "locked_cat" and cat_ep is None: cat_ep = {"start": t, "n": 0, "cat": 0, "pm_max": 0}
        if cat_ep is not None:
            cat_ep["n"] += 1; cat_ep["cat"] += v["cat"]; cat_ep["pm_max"] = max(cat_ep["pm_max"], pm)
            if v["lock"] != "locked_cat": cat_ep["end"] = t; cats.append(cat_ep); cat_ep = None
        # ⓑ·ⓒ: danger 시작 → 열 위험 아래 → SMOKE 풀림 → ACK_WAIT(① 10 s 시작) → 해제
        if d is None and v["hazard"] == "danger" and (prev is None or prev["hazard"] != "danger"):
            d = {"start": t, "pm": pm}
        elif d is not None:
            if "hot" not in d and "TEMP_DANGER" not in v["reasons"]: d["hot"] = t
            if "smoke" not in d and "SMOKE" not in v["reasons"]: d["smoke"] = t
            if "ack" not in d and "ACK_WAIT" in v["reasons"]: d["ack"] = t
            if "ready" not in d and p.get("lock_release_left_s") == 0: d["ready"] = t
            if v["lock"] == "unlocked": d["unlock"] = t; dangers.append(d); d = None
        prev = v
    if d is not None: dangers.append(d)
    if cat_ep is not None: cats.append(cat_ep)

    print("  -- 요약 (시각은 이 PC 기준 KST, 기기 시계가 맞을 때만 스톱워치와 비교)")
    for t in highs: print(f"  ⓐ high 시작 {hms(t)} — 손난로 투입 시각을 빼서 적음")
    for e in cats:
        end = hms(e["end"]) if "end" in e else "끝 안 남"
        print(f"  L4 locked_cat {hms(e['start'])} → {end} · 그동안 cat {e['cat']}/{e['n']} tick · PM2.5 최고 {e['pm_max']:.0f}")
    for e in dangers:
        s = e["start"]
        f = lambda k: f"+{e[k] - s:.0f} s" if k in e else "없음"
        print(f"  ⓑ danger {hms(s)} (PM2.5 {e['pm']:.0f}) — 65 °C 전환 시각을 빼서 적음")
        print(f"  ⓒ 열 위험 아래 {f('hot')} · SMOKE 풀림 {f('smoke')} · ACK_WAIT {f('ack')} · ① 0초 {f('ready')} · 해제 {f('unlock')}")
    by_kind = {}
    for _, k in drops: by_kind[k] = by_kind.get(k, 0) + 1
    print(f"  연결 끊김 {len(drops)}줄 {by_kind or ''}")
    for t, k in drops: print(f"    {hms(t)} {k}")
    print(f"  vision 2 s 만료 {len(expires)}번")
    for t in expires: print(f"    {hms(t)}")
