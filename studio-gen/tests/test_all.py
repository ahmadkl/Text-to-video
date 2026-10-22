import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studiogen import comfy_backend, ltx_backend, prompts, screenplay, stitch

EX = Path(__file__).resolve().parents[1] / "examples"
CHARS = prompts.load_characters(EX / "characters.json")
SCENES = screenplay.parse((EX / "scene.txt").read_text(encoding="utf-8"), list(CHARS))


def test_scene_header():
    assert len(SCENES) == 1 and SCENES[0].number == 7 and "باب" in SCENES[0].setting


def test_flat_paragraph_is_split_per_speaker():
    speakers = [s.speaker for s in SCENES[0].shots]
    assert speakers == ["علي", "عبد الله", "أبو عامر", "عامر"]


def test_longest_name_wins():
    # "أبو عامر" must not be read as "عامر"
    assert SCENES[0].shots[2].speaker == "أبو عامر"


def test_stage_direction_attached_to_next_line():
    assert "بغضب" in SCENES[0].shots[0].action
    assert "المخطط" in SCENES[0].shots[3].action


def test_frames_valid_for_ltx():
    for s in SCENES[0].shots:
        assert (s.num_frames - 1) % 8 == 0 and s.seconds <= 10


def test_prompt_contains_dialogue_and_look():
    p = prompts.shot_prompt(SCENES[0].shots[0], CHARS)
    assert "ليش سكرت الطريق" in p and "brown jacket" in p and "Levantine" in p


def test_ltx_command():
    cmd = ltx_backend.build_command(ltx_backend.LTXConfig(), "x", "o.mp4", 121, 1, image="a.png")
    assert cmd[cmd.index("--image") + 1:cmd.index("--image") + 4] == ["a.png", "0", "0.9"]
    assert "ltx_pipelines.distilled" in cmd and "--quantization" in cmd


def test_workflow_placeholders_keep_types():
    wf = comfy_backend.fill_workflow(comfy_backend.DEFAULT_WORKFLOW, prompt="p", negative="n", seed=5,
                                     width=64, height=96, checkpoint="m.safetensors")
    assert wf["3"]["inputs"]["seed"] == 5 and wf["5"]["inputs"]["width"] == 64
    assert wf["4"]["inputs"]["ckpt_name"] == "m.safetensors" and "{{" not in json.dumps(wf)


def test_stitch_mixed_clips(tmp_path):
    a, b = str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=red:s=320x240:d=1",
                    "-f", "lavfi", "-i", "sine=d=1", "-shortest", a], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=blue:s=640x360:d=1", b],
                   check=True)  # no audio, other size
    out = stitch.concat([a, b], str(tmp_path / "o.mp4"), 256, 448, 24)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                               capture_output=True, text=True).stdout)
    assert 1.8 < dur < 2.3


def test_voices_distinct_and_deterministic():
    from studiogen import tts_backend as T
    v = T.assign_voices(["a", "b", "c", "d"])
    assert len({(x["voice"], x["pitch"]) for x in v.values()}) == 4
    assert v == T.assign_voices(["a", "b", "c", "d"])
    o = T.assign_voices(["a"], {"a": {"voice": "ar-SA-HamedNeural", "pitch": None, "rate": None}})
    assert o["a"]["voice"] == "ar-SA-HamedNeural"
    f = T.assign_voices(["x"], female={"x"})
    assert "Amany" in f["x"]["voice"]


def test_lite_frames_and_command():
    from studiogen import ltx_lite_backend as L
    c = L.LiteConfig()
    assert (c.frames_for(5.0) - 1) % 8 == 0 and c.frames_for(99) == c.frames_for(10)
    cmd = L.build_command(c, "p", "out", 121, 3, image="a.png")
    assert "--offload_to_cpu" in cmd and cmd[cmd.index("--conditioning_start_frames") + 1] == "0"
    assert "ltxv-2b-0.9.8-distilled.yaml" in " ".join(cmd)


def test_mux_audio_freezes_video_to_speech_length(tmp_path):
    v, a, o = str(tmp_path / "v.mp4"), str(tmp_path / "a.mp3"), str(tmp_path / "o.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=green:s=320x240:d=1", v], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=3", a], check=True)
    stitch.mux_audio(v, a, o)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", o],
                               capture_output=True, text=True).stdout)
    assert 3.2 < dur < 3.7


def test_character_new_fields_optional():
    c = prompts.Character(name="x", look="l", voice="v")
    assert c.female is False and c.tts_voice is None
