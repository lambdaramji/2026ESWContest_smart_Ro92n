# rehearsal_times.py 시험: 만든 Core 로그 한 개로 ⓒ 시각·끊김 셈·주소 가림을 본다.
# 사용: python3 -I inbox/김병직/tools/rehearsal_times_test.py   → 마지막 줄 ok
import json, os, re, subprocess, sys, tempfile
from pathlib import Path


def state(t, ind=0, hazard="low", lock="unlocked", presence="present", fresh=True, reasons=(), left=None):
    p = {"induction": {"level": ind}, "fan": {"level": 1}, "hazard": hazard, "lock": lock, "presence": presence,
         "lock_release_left_s": left, "vision": {"fresh": fresh, "person": True, "cat": False}, "reasons": list(reasons),
         "sensors": {"pm": {"pm25": 10}, "thermal": {"metric": 2.0}}}
    return f"{t} atlas safetycore[1]: [state] " + json.dumps({"p": p})


hot = ("TEMP_DANGER", "SMOKE")
log = "\n".join([
    "100",  # 받기 명령의 첫 줄 date +%s
    state(100),
    "101 atlas safetycore[1]: [tcp] 192.0.2.5:5000 hello (vision)",
    "101 atlas safetycore[1]: [tcp] 192.0.2.5:5001 hello (ui)",      # Pi 5 앱 (vision과 같은 주소)
    "101 atlas safetycore[1]: [tcp] 198.51.100.7:7000 hello (ui)",   # 다른 기기의 앱 (정인 Mac 등)
    state(102, ind=2, hazard="high"),
    state(103, ind=0, hazard="danger", lock="locked_danger", reasons=hot),
    state(104, ind=0, hazard="danger", lock="locked_danger", presence="unknown", fresh=False, reasons=hot),
    "105 atlas safetycore[1]: [tcp] 192.0.2.5:5000 끊음: eof",
    "106 atlas safetycore[1]: [tcp] 192.0.2.9:6000 끊음: recv error",  # 로그 시작 전에 붙은 연결
    "107 atlas safetycore[1]: [tcp] 192.0.2.5:5001 끊음: eof",
    "108 atlas safetycore[1]: [tcp] 198.51.100.7:7000 끊음: eof",
    # 끊김이 먼저 오고 같은 주소의 vision hello가 뒤에 와도 Pi 5 앱이다(회차 중 다시 붙는 경우)
    "108 atlas safetycore[1]: [tcp] 192.0.2.6:5002 hello (ui)",
    "109 atlas safetycore[1]: [tcp] 192.0.2.6:5002 끊음: eof",
    "111 atlas safetycore[1]: [tcp] 192.0.2.6:5003 hello (vision)",
    state(110, hazard="mid", lock="locked_danger", reasons=("ACK_WAIT",), left=10),
    # ① 남은 초는 Core가 센다 — 중간에 다시 세면 ACK_WAIT + 10과 달라진다
    state(118, hazard="mid", lock="locked_danger", reasons=("ACK_WAIT",), left=4),
    state(122, hazard="mid", lock="locked_danger", reasons=("ACK_WAIT",), left=0),
    state(125),
]) + "\n"

with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "core.log")
    Path(path).write_text(log, encoding="utf-8")
    script = Path(__file__).with_name("rehearsal_times.py")
    out = subprocess.run([sys.executable, "-I", str(script), path], capture_output=True, text=True, check=True,
                         env={**os.environ, "TZ": "Asia/Seoul"}).stdout

# danger 103 → 열·연기 풀림·ACK_WAIT 110(+7), ① 0초는 state lock_release_left_s가 0이 된 122(+19), 해제 125(+22)
assert "ⓒ 열 위험 아래 +7 s · SMOKE 풀림 +7 s · ACK_WAIT +7 s · ① 0초 +19 s · 해제 +22 s" in out, out
assert "ⓐ high 시작" in out, out
# ui는 vision·voice와 같은 주소면 Pi 5 앱, 아니면 다른 기기(리허설 중 Mac 앱이 끊겨도 Pi 5 끊김으로 세지 않게)
assert "연결 끊김 5줄 {'vision': 1, '앞선 접속': 1, 'ui(Pi 5)': 2, 'ui(다른 기기)': 1}" in out, out
assert "vision 2 s 만료 1번" in out, out
# 기기 주소는 댓글·저장소에 남기지 않는다(CLAUDE.md 환경값) — 출력에 IP가 없어야 한다
assert not re.search(r"\d+\.\d+\.\d+\.\d+", out), out
print("ok")
