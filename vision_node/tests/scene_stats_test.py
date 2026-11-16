"""#215 scene check: counts follow SafetyCore's rules (person at P_person_score, cat slot hit = score ∧ ROI overlap)."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

tool = Path(__file__).resolve().parents[1] / "tools" / "scene_stats.py"
work = Path(tempfile.mkdtemp())
roi = work / "roi.json"
roi.write_text(json.dumps({"frame_w": 640, "frame_h": 480, "rotate": 0, "points": [[0, 240], [640, 240], [640, 480], [0, 480]]}))
profile = work / "profile.json"
profile.write_text(json.dumps({"P_person_score": 0.5, "P_cat_score": 0.35, "P_cat_overlap": 0.25, "P_cat_hits": 5, "P_cat_absent_s": 5.0}))

inside, outside = [100, 300, 200, 400], [100, 10, 200, 100]   # cat box in / out of the lower-half ROI


def frame(i, person=None, cat=None, dog=None, box=inside):
    best = {"person": {"score": person, "bbox": [500, 100, 600, 300]} if person else None,
            "cat": {"score": cat, "bbox": box} if cat else None,
            "dog": {"score": dog, "bbox": box} if dog else None, "teddy bear": None}
    return {"seq": i + 1, "t_ms": 1000 + 300 * i, "frame_w": 640, "frame_h": 480, "infer_ms": 280, "best": best}


def run(frames, *extra):
    log = work / "rec.jsonl"
    log.write_text("".join(json.dumps(f) + "\n" for f in frames))
    out = subprocess.run([sys.executable, str(tool), "--roi", str(roi), "--profile", str(profile), *extra, str(log)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


# Empty scene: a floor reflection below the threshold is not presence, but a margin under 0.05 is flagged.
empty = [frame(i, person=0.457 if i % 3 == 0 else None) for i in range(10)]
out = run(empty, "--expect", "empty")
assert "사람 ≥ 0.5: 0장" in out and "최고 0.4570" in out, out
assert "반사·조명" in out, "a top score within 0.05 of P_person_score is a warning, not a pass"
out = run([frame(i, person=0.30) for i in range(10)], "--expect", "empty")
assert "통과" in out and "여유 0.20" in out, out
out = run([frame(0, person=0.62)] + [frame(i) for i in range(1, 10)], "--expect", "empty")
assert "사람 ≥ 0.5: 1장" in out and "실패" in out, out

# Person gaps are between neighbouring detections only (no lead-in or tail).
out = run([frame(0, person=.9), frame(1, person=.9), frame(5, person=.9), frame(6, person=.4)])
assert "가장 긴 끊김 1.20 s" in out, out

# Cat: the slot is the best of cat+dog; a box outside the ROI is not a hit; any miss resets the run.
cats = [frame(i, cat=.8) for i in range(4)] + [frame(4, cat=.8, box=outside)] + [frame(i, dog=.6) for i in range(5, 11)]
out = run(cats, "--expect", "cat")
assert "고양이 hit 10장" in out and "가장 긴 연속 6장" in out, out
assert f"처음 5연속 {(9 * 300) / 1000:.1f} s" in out, "first run of 5 ends at frame 9 (frames 5..9)"
assert "통과" in out, out
out = run([frame(i, cat=.8 if i % 2 else .2) for i in range(12)], "--expect", "cat")
assert "5연속 없음" in out and "실패" in out, out

print("scene_stats ok")
