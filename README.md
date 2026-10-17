# Text-to-Video — فيديو من نص

يحوّل نصًا إلى فيديو MP4: خلفيات متحركة، نص عربي أو إنجليزي، تعليق صوتي (اختياري)، وملف ترجمة `.srt`.
كل **فقرة = مشهد**، والسطر الأول إن بدأ بـ `# ` صار بطاقة العنوان.

> هذا المشروع يصنع فيديوهات نصية متحركة (قصص، دروس، اقتباسات). وهو **لا** يولّد لقطات واقعية بالذكاء الاصطناعي.

## التشغيل من الجوال (بدون تثبيت شيء)

**الطريقة 1 — Issue (الأسهل):**
1. افتح تبويب **Issues** ← **New issue**.
2. اجعل العنوان يبدأ بكلمة `video` (مثل: `video قصة الفضاء`).
3. اكتب في النص قصتك: افصل المشاهد بسطر فارغ.
4. بعد دقائق يُضاف تعليق على الـ Issue برابط الفيديو.

**الطريقة 2 — Actions:**
**Actions ← Generate video ← Run workflow**، ثم اكتب النص في خانة `prompt` (افصل المشاهد بـ ` || `)، واختر الأبعاد والصوت.

الفيديو يظهر في **Releases** جاهزًا للتنزيل، وفي Artifacts داخل تشغيل الـ workflow.

> الـ Issue يعمل فقط إذا فتحه مالك المستودع.

## التشغيل على جهازك

```bash
sudo apt install ffmpeg fonts-noto-core      # أو ما يعادلها
pip install -r requirements.txt
python text_to_video.py -f examples/arabic.txt -o output/video.mp4
```

أمثلة:

```bash
python text_to_video.py -t "مرحبا || هذا هو المشهد الثاني" --aspect 9:16
python text_to_video.py -f examples/english.txt --voice en-GB-RyanNeural
python text_to_video.py -f examples/arabic.txt --no-voice
```

| الخيار | الوصف |
|---|---|
| `--aspect` | `16:9` (افتراضي) أو `9:16` للجوال أو `1:1` |
| `--voice` | `auto` يختار صوتًا عربيًا أو إنجليزيًا حسب النص، أو اسم صوت مثل `ar-EG-SalmaNeural`، أو `none` |
| `--rate` | سرعة الصوت، مثل `-10%` أو `+15%` |
| `--min-scene` | أقل مدة للمشهد بالثواني (3 افتراضيًا) |
| `--max-chars` | الفقرة الأطول تُقسَّم إلى مشاهد |
| `--seed` | لتثبيت الألوان والحركة |

أصوات عربية: `ar-SA-HamedNeural`، `ar-SA-ZariyahNeural`، `ar-EG-SalmaNeural`، `ar-EG-ShakirNeural`.
القائمة الكاملة: `edge-tts --list-voices`.

## ملاحظات

- التعليق الصوتي يستخدم `edge-tts` ويحتاج اتصالًا بالإنترنت. إن فشل يُنتَج الفيديو بدون صوت مع تحذير.
- لخط عربي مخصص: ضع ملف `.ttf` داخل مجلد `fonts/` أو اضبط المتغير `FONT_PATH`.
- كل فيديو يُنتج أيضًا `video.srt` بالترجمة.
