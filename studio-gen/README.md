# studio-gen — من نص السيناريو إلى فيديو بحوار

يحوّل سيناريو عربي إلى لقطات، ثم يولّد كل لقطة **بالصورة والصوت معاً** عبر LTX-2.5، ثم يدمجها في فيديو واحد.

```
scene.txt ──► screenplay.py (مشاهد + لقطات + متحدث) ──► prompts.py (وصف سينمائي + الحوار بين علامتي اقتباس)
   ComfyUI (صور شخصيات ثابتة الوجه) ──► ltx_backend.py (LTX-2.5: فيديو + صوت متزامن) ──► stitch.py ──► final.mp4
```

## التشغيل
```bash
git clone https://github.com/Lightricks/LTX-2 && cd LTX-2 && uv sync --extra natten && cd ..
hf auth login
hf download Lightricks/LTX-2.5 diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors \
  text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors vae/ltx-2.5-video-vae-bf16.safetensors \
  vae/ltx-2.5-audio-vae-bf16.safetensors \
  latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors --local-dir LTX-2/models/ltx-2.5

python -m studiogen.cli plan    examples/scene.txt -c examples/characters.json        # خطة اللقطات فقط (بدون GPU)
python -m studiogen.cli portraits -c examples/characters.json --checkpoint <ckpt>    # اختياري: يحتاج ComfyUI شغّال
python -m studiogen.cli render  examples/scene.txt -c examples/characters.json --ltx-repo LTX-2 --dry-run
```
احذف `--dry-run` للتوليد الفعلي. اللقطات المنتهية تُتخطى عند إعادة التشغيل.

## صيغة السيناريو
`المشهد 7 — المكان` ثم أسطر `الاسم: الحوار`، والأقواس `(...)` وصف حركة يُلصق باللقطة التالية.
النص المتصل في فقرة واحدة يُقطَّع تلقائياً عند كل اسم شخصية معروف. الأسماء تُعرَّف في `characters.json` (الشكل بالإنجليزية والصوت).

## تنبيهات صريحة
- **اللهجة:** لم أتحقق أن LTX-2.5 ينطق العربية الشامية جيداً. جرّب لقطة واحدة أولاً. إن كان النطق ضعيفاً فالبديل خط الصوت المنفصل (TTS) ثم خط `A2VidPipeline` الموجود في المستودع نفسه.
- **العتاد:** النموذج 22B والملفات ~66 جيجا، ومتطلبات VRAM غير مذكورة في المستودع. غالباً يلزم كرت كبير (A100/H100) أو تحويل fp8 مع offload. Colab المجاني (T4) لن يكفي.
- **المدة:** اللقطة الواحدة حتى ~10 ثوانٍ. الأطول تُبنى بدمج لقطات (الشخصية تُثبَّت بصورة مرجعية في الإطار الأول).
- **الترخيص:** LTX-2.5 تحت ترخيص LTX Community (راجع `LICENSE-2_x` في مستودعهم) قبل أي استخدام تجاري.

## من الجوال بدون GPU (خدمة سحابية مدفوعة بالثانية)
- `--backend fal` (مفتاح `FAL_KEY` من fal.ai) أو `--backend api` (مفتاح `LTX_API_KEY` من docs.ltx.io). التكلفة التقريبية 0.09$ للثانية بدقة 720p عمودي، وأقل لقطة 6 ثوانٍ.
- أضف المفتاح في **Settings ← Secrets and variables ← Actions** باسم `FAL_KEY`، ثم **Actions ← LTX video with dialogue (fal.ai) ← Run workflow**. ابدأ بـ `dry_run` ثم `max_shots=1`.
- هذا المسار نص فقط (بدون صورة مرجعية)، فثبات الوجوه يعتمد على وصف الشخصية في `characters.json`.

## المسار المجاني بالكامل (`--backend free`)
صوت شامي مجاني (edge-tts: ar-SY وar-LB وar-JO، لكل شخصية صوت ودرجة مختلفة) + فيديو بـ LTX-Video 2B على GPU مجاني (Kaggle أو Colab) + دمج بـ ffmpeg. دفتر التشغيل: `notebooks/free-gpu.ipynb`.
- اللقطة تطول بقدر الحوار (حتى 10 ثوانٍ)، والصوت يُلصق على فيديو صامت: **حركة الفم لا تتطابق مع الكلام تماماً**.
- جودة LTX-Video 2B أقل بوضوح من LTX-2.5 وVeo.
- edge-tts خدمة غير رسمية من مايكروسوفت قد تتغير أو تُحجب. أسماء الأصوات: `edge-tts --list-voices | grep ar-`، ويمكن تحديد `tts_voice` و`tts_rate` و`tts_pitch` و`female` لكل شخصية في `characters.json`.
- لم يُجرَّب على GPU حقيقي بعد؛ جرّب لقطة واحدة أولاً (`--max-shots 1`).
