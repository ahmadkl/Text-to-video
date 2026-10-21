"""Command line: screenplay text -> shots -> clips with dialogue -> one video.

    python -m studiogen.cli plan     scene.txt -c characters.json
    python -m studiogen.cli portraits -c characters.json --comfy http://127.0.0.1:8188 --checkpoint model.safetensors
    python -m studiogen.cli render   scene.txt -c characters.json --ltx-repo ../LTX-2 [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import comfy_backend, ltx_backend, prompts, screenplay, stitch


def _load(args):
    chars = prompts.load_characters(args.characters)
    style = prompts.load_style(args.characters)
    text = Path(args.script).read_text(encoding="utf-8") if getattr(args, "script", None) else ""
    scenes = screenplay.parse(text, list(chars)) if text else []
    return chars, style, scenes


def cmd_plan(args):
    chars, style, scenes = _load(args)
    plan = []
    for sc in scenes:
        for sh in sc.shots:
            plan.append({"scene": sc.number, "shot": sh.index, "speaker": sh.speaker, "line": sh.line,
                         "seconds": round(sh.seconds, 1), "frames": sh.num_frames,
                         "prompt": prompts.shot_prompt(sh, chars, style, args.setting_en)})
    Path(args.out).write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(p["seconds"] for p in plan)
    print(f"{len(plan)} shots, ~{total:.0f}s total -> {args.out}")


def cmd_portraits(args):
    chars = prompts.load_characters(args.characters)
    client = comfy_backend.ComfyClient(args.comfy)
    for i, c in enumerate(chars.values()):
        out = f"{args.out_dir}/{c.name}.png"
        comfy_backend.make_portrait(client, c.look, out, seed=100 + i, checkpoint=args.checkpoint,
                                    workflow_path=args.workflow)
        print("portrait:", out)


def cmd_render(args):
    chars, style, scenes = _load(args)
    cfg = ltx_backend.LTXConfig(ltx_repo=args.ltx_repo, models_dir=args.models_dir)
    clips = []
    for sc in scenes:
        for sh in sc.shots:
            out = f"{args.out_dir}/s{sc.number:02d}_{sh.index:02d}.mp4"
            ref = chars[sh.speaker].reference_image if sh.speaker in chars else None
            if ref is None and sh.speaker:
                cand = Path(args.portraits) / f"{sh.speaker}.png"
                ref = str(cand.resolve()) if cand.exists() else None
            if not Path(out).exists() or args.dry_run:  # resume: skip finished shots
                ltx_backend.generate_shot(cfg, prompts.shot_prompt(sh, chars, style, args.setting_en),
                                          str(Path(out).resolve()), sh.num_frames,
                                          seed=args.seed + sh.index, image=ref, dry_run=args.dry_run)
            clips.append(out)
    if not args.dry_run:
        print("final:", stitch.concat(clips, args.final, cfg.width, cfg.height, cfg.fps))


def main():
    ap = argparse.ArgumentParser(prog="studiogen")
    sub = ap.add_subparsers(required=True)
    p = sub.add_parser("plan"); p.add_argument("script"); p.add_argument("-c", "--characters", required=True)
    p.add_argument("--setting-en", default=""); p.add_argument("--out", default="plan.json"); p.set_defaults(f=cmd_plan)
    p = sub.add_parser("portraits"); p.add_argument("-c", "--characters", required=True)
    p.add_argument("--comfy", default="http://127.0.0.1:8188"); p.add_argument("--checkpoint", default="")
    p.add_argument("--workflow"); p.add_argument("--out-dir", default="portraits"); p.set_defaults(f=cmd_portraits)
    p = sub.add_parser("render"); p.add_argument("script"); p.add_argument("-c", "--characters", required=True)
    p.add_argument("--ltx-repo", default="LTX-2"); p.add_argument("--models-dir", default="models/ltx-2.5")
    p.add_argument("--setting-en", default=""); p.add_argument("--portraits", default="portraits")
    p.add_argument("--out-dir", default="clips"); p.add_argument("--final", default="final.mp4")
    p.add_argument("--seed", type=int, default=42); p.add_argument("--dry-run", action="store_true")
    p.set_defaults(f=cmd_render)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
