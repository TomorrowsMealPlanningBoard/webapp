# Gemini Veo 動画生成ガイド — TomorrowsMeal 冒頭10秒フック

## 概要

デモ動画の **S1 冒頭10秒フック**をGemini Veo 2で生成する手順書。

**狙い**: 献立を考える認知負荷（栄養・在庫・好み・重複排除を1日3回）を「主婦の表情」で伝え、スマホに3案が並ぶ解放感で落とす。

10秒を**2セグメント（各5秒）**に分割して生成し、ffmpegで結合する。単一10秒生成より大幅に安定する。

| セグメント | 尺 | カット | 内容 |
|---|---|---|---|
| **Segment A** | 0–5s | C1+C2 | 冷蔵庫を開け中を見る → 悩む顔（痛みの半分） |
| **Segment B** | 5–10s | C3+C4 | 「何作ろう…」と独り言 → スマホ通知が来る → 表情が和らぐ（解決の半分） |

---

## Gemini Veo ベストプラクティス

1. **セグメントを5秒以内に分割**: 10秒一発生成は後半が崩れやすい。5秒×2本が最安定。
2. **スタイルワードをプロンプト冒頭に配置**: `Cinematic, photorealistic, warm kitchen lighting` を先頭に書く。
3. **カメラ動作と人物動作は1セグメント1アクション**: 詰め込みすぎると破綻する。
4. **人物定義を両セグメントで一字一句統一**: キャラクターの顔・服装・髪型の描写を全コピーする。
5. **Seed値を固定**: 同じSeedで生成するとキャラクター外見が安定しやすい。
6. **ネガティブプロンプトを必ず使う**: 洋食・家族・CGI風など不要な要素を明示除外する。
7. **生成後に目視確認**: C4のスマホ画面が英語UIになっていないか、家族が映り込んでいないか確認。

---

## キャラクター定義（全セグメント共通・コピー用）

```
Japanese woman, 30 years old, beautiful, naturally attractive face,
black hair (shoulder length, slightly disheveled from a long workday),
wearing casual home clothes (simple knit top, soft dark trousers),
expression of mild fatigue after returning from work,
she is alone, no other family members present
```

---

## Segment A（0–5秒）— C1+C2

### 目的
- **C1 (0–3s)**: 主婦が日本の家庭用冷蔵庫を開けて中をのぞき込む
- **C2 (3–5s)**: 冷蔵庫内の日本食材アップ → 悩む顔のクローズアップ

### 英語プロンプト（推奨）

```
Cinematic, photorealistic, warm and realistic home kitchen lighting, 16:9, 5 seconds.

A beautiful Japanese woman, 30 years old, black shoulder-length hair slightly disheveled,
wearing casual home clothes (simple knit top, soft dark trousers), mild fatigue after work.
She stands in a tidy modern Japanese home kitchen with a small countertop. She opens the
full-size refrigerator door with one hand and looks inside with a thoughtful, subtly troubled
expression. The refrigerator is well-stocked with typical Japanese supermarket items: chicken
thighs in a tray, thinly sliced pork, a salmon fillet, fresh cabbage, onion, carrots, a carton
of eggs, tofu in water packaging, and a milk carton — a realistic "moderate household" amount,
not overstuffed. Camera: medium shot from waist up as she opens the door (0–3s), then slowly
cuts or pushes in to show the inside of the refrigerator and a soft close-up of her face,
eyes scanning the contents with a subtly worried look (3–5s). No other people.
Warm, realistic interior lighting. Quiet domestic atmosphere.

Negative prompt: animation, cartoon, CGI look, western food, foreign ingredients,
luxury ingredients, children, husband, family, overstuffed fridge, fish-eye lens,
dramatic lighting, text overlay, logo
```

### 日本語プロンプト（参考）

```
シネマティック、フォトリアル、16:9、5秒。
日本の家庭のキッチン。30歳前後の清潔感のある美しい主婦が一人で、
仕事終わりの少し疲れた様子で冷蔵庫（日本の家庭用フルサイズ）のドアを開ける。
中をのぞき込み「何を作ろう」と独り言を言う考え込む表情。
冷蔵庫の中：鶏もも肉（トレイ）、薄切り豚肉、鮭の切り身、キャベツ、玉ねぎ、
にんじん、卵（パック）、豆腐（水入りパック）、牛乳パック。
普通の家庭の"ほどほどに入った"冷蔵庫の生活感。
0–3s：腰上のミディアムショットで冷蔵庫を開ける。
3–5s：冷蔵庫内と悩む顔のクローズアップへゆっくりカット。
暖かいリアルな室内照明。一人、家族なし。

NG：アニメ、洋食、外国食材、子供、夫、詰め込みすぎの冷蔵庫、CGI風、テキスト
```

---

## Segment B（5–10秒）— C3+C4

### 目的
- **C3 (0–2s of clip)**: 冷蔵庫を眺めながら「何作ろう…」と小さく独り言
- **C4 (2–5s of clip)**: 手元のスマホに通知が来る → 画面を見ると3案 → 表情がほぐれる

> **シナリオのポイント**: ため息で受動的に悩む→解決ではなく、AIが**能動的に通知を送ってくる**という構図にする。「指示を待たず先回りするエージェント」の本質を冒頭10秒で視覚的に伝える。

### 英語プロンプト（推奨）

```
Cinematic, photorealistic, warm home kitchen lighting, 16:9, 5 seconds.

Continuation. The same Japanese woman, 30 years old, beautiful, black shoulder-length hair
slightly disheveled, wearing casual home clothes (simple knit top, soft dark trousers),
mild work fatigue on her face. She stands in the kitchen looking at the closed refrigerator.
At the start (0–2s), she murmurs quietly to herself in Japanese — a soft, almost inaudible
"何作ろう…" under her breath, still staring at the fridge with a slightly lost expression.
Then (2–5s), her smartphone on the kitchen counter lights up with a notification. She glances
down. The phone screen shows a clean app interface with three Japanese meal suggestion cards
neatly listed (Japanese characters visible, card-based UI aesthetic, not readable). Her expression
softens immediately — a quiet "oh" of pleasant surprise, subtle relief, the look of someone
unexpectedly helped. Camera: medium close-up on her face and phone. Warm, realistic lighting.

Negative prompt: big smile, laughter, tears, dramatic gesture, animation, cartoon, CGI look,
children, husband, family, western food UI, English text on phone, music-video style,
text overlay, logo
```

### 日本語プロンプト（参考）

```
シネマティック、フォトリアル、16:9、5秒。前シーンの続き。
同じ30歳の美しい主婦（黒髪、部屋着、残業後の疲れ）。
キッチンに立ち、閉まった冷蔵庫を眺めている。
0–2s：「何作ろう…」と小さく独り言。ほぼ聞こえない程度のつぶやき。
　　　冷蔵庫を見ながら少し途方に暮れた表情。
2–5s：キッチンカウンターのスマホに通知が届く。画面が光る。
　　　手元を見ると、日本語のカードUIで3つの献立候補が並んでいる。
　　　（文字は判読不要、カードの雰囲気だけ伝える）
　　　表情がすっとほぐれる。「あ」という小さな驚きと安堵。思わぬ助けをもらった顔。
ミディアムクローズアップ。暖かい室内照明。

NG：大笑い、泣く、アニメ、子供、夫、洋食UI、英語表記スマホ、劇的な動き、CGI風
```

---

## 生成設定（Gemini AI Studio / Vertex AI）

| 設定 | 推奨値 |
|---|---|
| Model | Veo 2（または利用可能な最新版） |
| Duration | **5秒**（各セグメント） |
| Aspect Ratio | **16:9** |
| Resolution | 1920×1080 |
| Seed | 固定（両セグメントで同じ値を試す） |
| Mode | Text-to-video |

> **Seedについて**: Segment AとBで同じSeed値を使うとキャラクター外見が揃いやすい。生成後に顔・服・髪型の一致を目視確認すること。不一致なら別のSeed値で再生成。

---

## 生成後の処理: ffmpegで結合

```bash
#!/bin/bash
# セグメントA・Bを結合して10秒のフック映像を作る

SEG_A="segment_a_0to5s.mp4"   # Gemini生成ファイル名に合わせて変更
SEG_B="segment_b_5to10s.mp4"  # Gemini生成ファイル名に合わせて変更
OUT="video-project/public/assets/gemini_hook.mp4"

# Step 1: 両セグメントを同一エンコード設定・フレームレートに揃える
ffmpeg -y -i "$SEG_A" \
  -c:v libx264 -preset fast -crf 20 -r 30 -vf "scale=1920:1080" \
  /tmp/seg_a_norm.mp4

ffmpeg -y -i "$SEG_B" \
  -c:v libx264 -preset fast -crf 20 -r 30 -vf "scale=1920:1080" \
  /tmp/seg_b_norm.mp4

# Step 2: concatリストを作成
cat > /tmp/concat_hook.txt <<EOF
file '/tmp/seg_a_norm.mp4'
file '/tmp/seg_b_norm.mp4'
EOF

# Step 3: 結合
ffmpeg -y -f concat -safe 0 -i /tmp/concat_hook.txt -c copy "$OUT"

echo "Done: $OUT"
ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$OUT"
```

---

## Remotion側への組み込み

生成した `gemini_hook.mp4` を所定の場所に置いて `Root.tsx` で有効化する:

```bash
# 配置
cp video-project/public/assets/gemini_hook.mp4 video-project/public/assets/seedance_hook.mp4
# ※ ファイル名は seedance_hook.mp4 のままでOK（コード変更不要）
```

または `Root.tsx` の `VIDEOS.seedance` を直接変更:

```typescript
// video-project/src/Root.tsx
const VIDEOS = {
  seedance: staticFile("assets/gemini_hook.mp4"),  // ← ここを有効化
  // ...
};
```

その後、`final_render.sh` で自動結合:

```bash
HOOK_SEC=10 ./scripts/record/final_render.sh
```

---

## よくある失敗と対策

| 問題 | 原因 | 対策 |
|---|---|---|
| Segment AとBで顔が別人になる | Seed未固定 / 人物描写が微妙に違う | Seedを固定。人物描写を一字一句コピー |
| 冷蔵庫の中身が洋食になる | 英語の"food"が欧米食にバイアス | "Japanese supermarket items" + NG: "western food, foreign ingredients" |
| スマホ画面が英語UIになる | デフォルトが英語 | "Japanese characters visible" + NG: "English text on phone" |
| ため息が大げさな演技になる | Veoは感情表現を誇張しやすい | "subtle", "quiet", "mild" を使いNG: "dramatic gesture" |
| C4でスマホが生成されない | 動作描写が弱い | "she glances down at her smartphone" を明示。画面UIの雰囲気も描写 |
| 10秒単一生成で後半が崩れる | 長い生成は安定しない | 必ず5秒×2本に分割 |

---

## 完成チェックリスト

- [ ] Segment A: 冷蔵庫を開ける動作が自然か
- [ ] Segment A: 冷蔵庫内に日本食材が見えるか（洋食・外国食材でないか）
- [ ] Segment B: Segment Aと同じ女性か（顔・服装・髪型の一致）
- [ ] Segment B: 「何作ろう…」の独り言 → スマホ通知 → 安堵の流れが読み取れるか
- [ ] Segment B: スマホ画面にカードUIらしきものが見えるか（英語UIでないか）
- [ ] 結合後: C2→C3の継ぎ目が不自然でないか
- [ ] 結合後: `ffprobe` で duration が約10秒であることを確認
- [ ] `video-project/public/assets/` に配置済みか
- [ ] `final_render.sh` で自動結合されることを確認
