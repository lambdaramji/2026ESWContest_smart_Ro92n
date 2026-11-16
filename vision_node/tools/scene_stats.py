#!/usr/bin/env python3
"""#215 현장 점검 — scene_check.sh가 받은 기록을 SafetyCore 판정값으로 센다 (PC 전용).

프레임은 저장하지 않으니 숫자만 본다(#72). 사람은 P_person_score 이상만 재실로, 고양이는 cat+dog 칸이
P_cat_score 이상 ∧ ROI 겹침 P_cat_overlap 이상이면 hit로 센다 — Core와 같은 기준(설계서 §3.4·§5.2).
  --expect empty  빈 장면(ⓔ): 사람 판정 0장이고 최고 점수가 기준에서 NEAR 넘게 떨어져야 통과
  --expect cat    인형이 있는 장면(ⓓ): P_cat_hits 연속 hit이 한 번이라도 나와야 통과(잠글 수 있음)

  python3 vision_node/tools/scene_stats.py --roi vision_node/config/roi.json --profile profiles/default.json [--expect empty|cat] <기록.jsonl>...
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detection_table import longest_run, pet, validate_roi  # noqa: E402

NEAR = 0.05   # 빈 장면 최고 점수가 기준까지 이만큼 안 남으면 반사·조명부터 본다 (#215 ⓔ, D-065 여유 0.04)


def gaps(times):
    """이웃한 감지 사이 간격 — 처음 감지 전·마지막 감지 뒤는 세지 않는다."""
    return [b - a for a, b in zip(times, times[1:])]


def report(path, points, p, expect):
    rows = [r for r in map(json.loads, open(path)) if "seq" in r]
    if not rows:
        print(f"== {Path(path).name}: 기록 없음")
        return
    t = [(r["t_ms"] - rows[0]["t_ms"]) / 1000 for r in rows]
    infer = [r["infer_ms"] for r in rows if isinstance(r.get("infer_ms"), (int, float))]
    head = f"== {Path(path).name}: {len(rows)}장 {t[-1]:.1f} s"
    if t[-1] > 0:
        head += f" ({(len(rows) - 1) / t[-1]:.2f}장/s)"
    if infer:
        head += f", 추론 중앙 {statistics.median(infer):.0f} ms"
    print(head)

    ps = p["P_person_score"]
    person = [r["best"].get("person") for r in rows]
    score = [x["score"] if x else 0.0 for x in person]
    top = max(range(len(rows)), key=lambda i: score[i])
    seen = [ti for ti, s in zip(t, score) if s >= ps]
    line = f"   사람 ≥ {ps}: {len(seen)}장 ({100 * len(seen) / len(rows):.1f} %) | 최고 {score[top]:.4f}"
    if person[top]:
        line += f" {person[top]['bbox']}"
    print(line + f" | {ps - NEAR:.2f} 이상 {sum(s >= ps - NEAR for s in score)}장")
    if len(seen) >= 2:
        print(f"   사람 감지 사이 가장 긴 끊김 {max(gaps(seen)):.2f} s (고양이 감시 {p['P_cat_absent_s']} s)")

    k = p["P_cat_hits"]
    slot = [pet(r, points) for r in rows]
    hit = [x is not None and x[0] >= p["P_cat_score"] and x[1] >= p["P_cat_overlap"] for x in slot]
    first = next((t[i] for i in range(k - 1, len(hit)) if all(hit[i - k + 1:i + 1])), None)
    line = f"   고양이 hit {sum(hit)}장 ({100 * sum(hit) / len(rows):.1f} %) | 가장 긴 연속 {longest_run(hit)}장 | "
    line += f"처음 {k}연속 {first:.1f} s" if first is not None else f"{k}연속 없음"
    hit_t = [ti for ti, h in zip(t, hit) if h]
    if len(hit_t) >= 2:
        line += f" | hit 사이 가장 긴 끊김 {max(gaps(hit_t)):.2f} s"
    print(line)

    if expect == "empty":
        if seen:
            print(f"   빈 장면 실패: 사람 판정 {len(seen)}장 — 시야 안 사람·사진·화면부터(증상 A), 없으면 반사 자리 {person[top]['bbox']}")
        elif score[top] >= ps - NEAR:
            print(f"   빈 장면 주의: 최고 {score[top]:.4f}, 기준까지 {ps - score[top]:.2f} — 그 자리 반사·조명을 먼저 바꾸고 다시 잼(기준 상향은 PM 결정)")
        else:
            print(f"   빈 장면 통과: 여유 {ps - score[top]:.2f}")
    elif expect == "cat":
        if first is None:
            print(f"   고양이 실패: {k}연속 hit이 없어 잠기지 않음 — 인형 자세·위치 확인, 안 되면 영상 클립")
        else:
            print(f"   고양이 통과: 감시가 시작돼 있으면 처음 {k}연속까지 {first:.1f} s")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--roi", required=True, help="기록할 때 쓴 roi.json (scene_check.sh가 Pi 5에서 함께 받음)")
    ap.add_argument("--profile", required=True, help="P_person_score·P_cat_* 값을 읽을 profile")
    ap.add_argument("--expect", choices=("empty", "cat"))
    ap.add_argument("logs", nargs="+")
    a = ap.parse_args()
    points = validate_roi(json.load(open(a.roi)))["points"]
    profile = json.load(open(a.profile))
    for log in a.logs:
        report(log, points, profile, a.expect)


if __name__ == "__main__":
    main()
