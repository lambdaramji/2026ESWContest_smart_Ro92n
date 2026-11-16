#!/usr/bin/env bash
# #215 현장 점검: Pi 5 vision 서비스를 잠깐 멈추고 **실행 중이던 인자 그대로** N장을 기록해 PC로 가져와 센다.
# 프레임(사진)은 저장하지 않는다 — 점수·상자 숫자만(#72). 기록 중 끊겨도 서비스는 다시 켠다.
#
#   vision_node/tools/scene_check.sh <이름> [장수=400] [empty|cat]
#   예) vision_node/tools/scene_check.sh hall-empty 400 empty   # ⓔ 시연장 조명, 빈 장면 사람 점수
#       vision_node/tools/scene_check.sh hall-smoke 400 cat     # ⓓ 인센스가 타는 중 인형
#
# 환경 변수: PI5(필수, 예: export PI5=root@<Pi5 IP>) OUT(기본 ~/hoodseek-measure/scene-오늘날짜)
#            PROFILE(기본 저장소 profiles/default.json — vision 판정값은 기기 profile과 같음)
# vision이 약 (장수 ÷ 3.5) 초 멈춘다. 팀에 먼저 알린다. 그동안 Core는 이 기록 실행이 보내는 vision을 그대로 받는다.
# 기록 중 PC에서 끊어도 Pi 5는 기록을 끝내고 서비스를 다시 켠다. 그때 남은 사본은 서비스가 active로 돌아온 뒤 지운다:
#   ssh "$PI5" 'rm -f /data/hoodseek/log/scene-*'
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUT=${OUT:-$HOME/hoodseek-measure/scene-$(date +%F)} PROFILE=${PROFILE:-$ROOT/profiles/default.json}
[[ $# -ge 1 && $# -le 3 ]] || { sed -n '2,13p' "$0"; exit 2; }
# ponytail: 실제 IP는 저장소에 두지 않는다 (설계서 §8.3)
PI5=${PI5:?"PI5를 지정하세요. 예: export PI5=root@<Pi5 IP>"}
name=$1 frames=${2:-400} expect=${3:-}
[[ $name =~ ^[A-Za-z0-9_-]+$ ]] || { echo "이름은 영문·숫자·_·-만" >&2; exit 2; }
[[ $frames =~ ^[1-9][0-9]*$ ]] || { echo "장수는 1 이상 정수" >&2; exit 2; }
case $expect in ""|empty|cat) ;; *) echo "세 번째 인자 = empty | cat" >&2; exit 2 ;; esac
mkdir -p "$OUT"
[[ ! -e $OUT/$name.jsonl ]] || { echo "이미 있는 이름: $OUT/$name.jsonl" >&2; exit 2; }

# 1. 읽기만: 서비스 인자 확인 + 지금 바이너리·모델·ROI 해시(기록에 남김)
info=$(ssh -o BatchMode=yes "$PI5" sh -s <<'REMOTE'
P=$(systemctl show hoodseek-vision-node -p MainPID --value)
[ "${P:-0}" -gt 0 ] || { echo "vision 서비스가 돌고 있지 않음" >&2; exit 1; }
A=$(tr '\0' ' ' < /proc/$P/cmdline)
case "$A" in *"--output /dev/null"*) ;; *) echo "서비스 인자에 --output /dev/null이 없음 — 멈추지 않음" >&2; exit 1 ;; esac
cd /data/hoodseek || exit 1
echo "bin $(sha256sum "${A%% *}" | cut -c1-16)"
for k in --model --roi; do v=$(echo "$A" | sed -n "s/.* $k \([^ ]*\).*/\1/p"); echo "$k $v $(sha256sum "$v" | cut -c1-16)"; done
REMOTE
)
roi=$(awk '$1 == "--roi" {print $2}' <<<"$info")
[[ -n $roi ]] || { echo "서비스 인자에 --roi가 없음" >&2; exit 1; }
[[ $roi == /* ]] || roi=/data/hoodseek/$roi   # 유닛은 절대 경로, 손으로 띄운 판은 상대 경로일 수 있음
{ echo "# $name $(date '+%F %T') ${frames}장"; echo "$info"; } | tee "$OUT/$name.txt"
read -r -p "vision을 약 $((frames * 2 / 7))초 멈춥니다. 팀에 알렸으면 Enter (취소 Ctrl+C) "

# 2. 서비스를 멈추고 같은 인자에 --output·--max-frames만 바꿔 기록 → 서비스 다시 켬
ssh -o BatchMode=yes "$PI5" sh -s -- "$name" "$frames" <<'REMOTE'
N=$1 F=$2
cd /data/hoodseek || exit 1
P=$(systemctl show hoodseek-vision-node -p MainPID --value)
RUN=$(tr '\0' ' ' < /proc/$P/cmdline | sed "s#--output /dev/null#--output log/scene-$N.jsonl --max-frames $F#")
mkdir -p log
trap 'systemctl start hoodseek-vision-node' EXIT
trap 'exit 1' HUP INT TERM
echo "$(date +%T) vision 멈춤, ${F}장 기록"
systemctl stop hoodseek-vision-node
LLVM_PROFILE_FILE=/dev/null $RUN 2> log/scene-$N.stderr; rc=$?
systemctl start hoodseek-vision-node
sleep 3
echo "$(date +%T) 기록 끝 rc=$rc, 서비스 $(systemctl is-active hoodseek-vision-node), 줄 수 $(wc -l < log/scene-$N.jsonl)"
[ "$rc" -eq 0 ] || tail -3 log/scene-$N.stderr >&2
REMOTE

# 3. 기록과 그때 ROI를 PC로 받고 Pi 5 사본은 지움 → 판정값으로 셈
scp -q -o BatchMode=yes "$PI5:/data/hoodseek/log/scene-$name.jsonl" "$OUT/$name.jsonl"
scp -q -o BatchMode=yes "$PI5:$roi" "$OUT/$name.roi.json"
ssh -o BatchMode=yes "$PI5" "rm -f /data/hoodseek/log/scene-$name.jsonl /data/hoodseek/log/scene-$name.stderr"
# 셀 때 쓴 판정값의 출처. Pi 4 기기 profile과 같은지는 배포 기록(#67·#72)의 해시와 사람이 대조한다
echo "profile $PROFILE $(sha256sum "$PROFILE" | cut -c1-16)" | tee -a "$OUT/$name.txt"
python3 -I "$ROOT/vision_node/tools/scene_stats.py" --roi "$OUT/$name.roi.json" --profile "$PROFILE" \
  ${expect:+--expect "$expect"} "$OUT/$name.jsonl" | tee -a "$OUT/$name.txt"
