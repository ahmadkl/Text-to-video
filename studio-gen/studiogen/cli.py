"""Command line: screenplay text -> shots -> clips with dialogue -> one video.

    python -m studiogen.cli plan     scene.txt -c characters.json
    python -m studiogen.cli portraits -c characters.json --comfy http://127.0.0.1:8188 --checkpoint model.safetensors
    python -m studiogen.cli render   scene.txt -c characters.json --ltx-repo ../LTX-2 [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import api_backend, comfy_backend, fal_backend, ltx_backend, ltx_lite_backend, prompts, screenplay, stitch, tts_backend


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


def _render_free(args, chars, style, scenes):
    """Free pipeline: Syrian-Arabic TTS (edge-tts) + silent LTX-Video 2B on a free GPU + ffmpeg."""
    lite = ltx_lite_backend.LiteConfig(repo=args.lite_repo, width=args.lite_width, height=args.lite_height)
    voices = tts_backend.assign_voices(
        list(chars),
        {n: {"voice": c.tts_voice, "rate": c.tts_rate, "pitch": c.tts_pitch} for n, c in chars.items()},
        {n for n, c in chars.items() if c.female})
    shots = [(sc, sh) for sc in scenes for sh in sc.shots]
    if args.max_shots:
        shots = shots[:args.max_shots]
    clips = []
    for sc, sh in shots:
        base = f"{args.out_dir}/s{sc.number:02d}_{sh.index:02d}"
        prompt = prompts.shot_prompt(sh, chars, style, args.setting_en, silent=True)
        if args.dry_run:
            v = voices.get(sh.speaker, {}).get("voice", "-")
            print(f"[{sc.number}.{sh.index}] {sh.speaker} voice={v}: {prompt[:140]}...")
            clips.append(base + ".mp4")
            continue
        if not Path(base + ".mp4").exists():  # resume
            audio = None
            if sh.speaker and sh.line:
                audio = tts_backend.synthesize(sh.line, voices[sh.speaker], base + ".mp3")
            seconds = (tts_backend.duration(audio) + 0.5) if audio else sh.seconds
            portrait = Path(args.portraits) / f"{sh.speaker}.png" if sh.speaker else None
            silent = ltx_lite_backend.generate_shot(lite, prompt, base + "_silent.mp4", seconds,
                                                    args.seed + sh.index,
                                                    str(portrait) if portrait and portrait.exists() else None)
            if audio:
                stitch.mux_audio(silent, audio, base + "_nosub.mp4")
                stitch.burn_subtitle(base + "_nosub.mp4", f"{sh.speaker}: {sh.line}", base + ".mp4")
            else:
                Path(silent).replace(base + ".mp4")
            print("done:", base + ".mp4", flush=True)
        clips.append(base + ".mp4")
    if not args.dry_run:
        print("final:", stitch.concat(clips, args.final, lite.width, lite.height, lite.fps))


def cmd_render(args):
    chars, style, scenes = _load(args)
    if args.backend == "free":
        return _render_free(args, chars, style, scenes)
    use_api = args.backend == "api"
    use_fal = args.backend == "fal"
    fcfg = fal_backend.FalConfig(resolution=args.resolution, aspect_ratio="16:9" if args.landscape else "9:16")
    acfg = api_backend.APIConfig(model=args.api_model, tier=args.resolution, portrait=not args.landscape)
    cfg = ltx_backend.LTXConfig(ltx_repo=args.ltx_repo, models_dir=args.models_dir)
    if use_api or use_fal:
        w, h = map(int, acfg.resolution.split("x"))  # same sizes as the hosted LTX tiers
        cfg.width, cfg.height = w, h
    shots = [(sc, sh) for sc in scenes for sh in sc.shots]
    if args.max_shots:
        shots = shots[:args.max_shots]
    if use_api:
        total = sum(acfg.cost(acfg.duration_for(sh.seconds)) for _, sh in shots)
        print(f"{len(shots)} shots, estimated cost ~${total:.2f} ({acfg.model}, {acfg.resolution})")
    elif use_fal:
        total = fal_backend.estimate_cost([fal_backend.pick_duration(sh.seconds) for _, sh in shots], fcfg.resolution)
        print(f"{len(shots)} shots, estimated cost ~${total:.2f} (fal.ai, {fcfg.resolution})")
    clips = []
    for sc, sh in shots:
        out = f"{args.out_dir}/s{sc.number:02d}_{sh.index:02d}.mp4"
        prompt = prompts.shot_prompt(sh, chars, style, args.setting_en)
        if args.dry_run:
            print(f"[{sc.number}.{sh.index}] {sh.speaker}: {prompt[:160]}...")
        elif not Path(out).exists():  # resume: skip finished shots
            if use_api:
                api_backend.generate_shot(acfg, prompt, out, sh.seconds)
            elif use_fal:
                fal_backend.generate_shot(fcfg, prompt, sh.seconds, out)
            else:
                ref = chars[sh.speaker].reference_image if sh.speaker in chars else None
                if ref is None and sh.speaker:
                    cand = Path(args.portraits) / f"{sh.speaker}.png"
                    ref = str(cand.resolve()) if cand.exists() else None
                ltx_backend.generate_shot(cfg, prompt, str(Path(out).resolve()), sh.num_frames,
                                          seed=args.seed + sh.index, image=ref)
            print("done:", out, flush=True)
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
    p.add_argument("--backend", choices=["local", "api", "fal", "free"], default="local",
                   help="local = big GPU, LTX-2.5; api = api.ltx.io (paid); fal = fal.ai (paid); free = TTS + LTX-Video 2B on a free GPU")
    p.add_argument("--lite-repo", default="LTX-Video"); p.add_argument("--lite-width", type=int, default=480)
    p.add_argument("--lite-height", type=int, default=832)
    p.add_argument("--api-model", default="ltx-2-5-fast")
    p.add_argument("--resolution", default="720p", help="720p or 1080p (api / fal backends)")
    p.add_argument("--landscape", action="store_true"); p.add_argument("--max-shots", type=int, default=0)
    p.set_defaults(f=cmd_render)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
