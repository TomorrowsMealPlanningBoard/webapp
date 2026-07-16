# Seedance 2.0 冒頭素材 制作指示書

デモ動画の **S1 冒頭10秒フック**用の映像素材。

> **狙い**: このプロジェクトが解決するのは「日々の献立を考える認知負荷」。献立を考える行為は、栄養バランス・食材在庫・好み・直近の重複排除といった制約に加え、その日の気分や体調、かけられる時間や手間まで同時に考慮する必要があり、非常に負荷が高い。しかもそれを **1日3回**繰り返す。フック映像はこの「考える辛さ（逡巡）」を主役にし、最後にスマホへ3案が並ぶ解放感で落とす。**食材が主役ではなく、悩む主婦の表情が主役**。

---

## 用途・要件

| 項目 | 内容 |
|---|---|
| 尺 | **10秒**（4カット構成。カット割りは下記） |
| 解像度 | 1920×1080 (16:9横) を基本。縦アプリ枠にはめる場合は 1080×1920 でも可 |
| 音声 | **なし**（映像のみ。BGM・ナレーションは後から付ける） |
| 登場人物 | **食事を作る主婦（30歳・清潔感のある美人）1名のみ**。家族・子供・複数人は出さない |
| トーン | リアル・生活感あり。アニメ/イラスト調は不可。優しく親しみやすい生活者目線 |
| 出力ファイル名 | `seedance_hook.mp4` |
| 配置先 | `video-project/public/assets/seedance_hook.mp4` |

---

## カット構成（10秒 / 4カット）

| カット | 尺 | 内容 |
|---|---|---|
| **C1** | 0–3s | 30歳の主婦が日本の家庭用冷蔵庫を開け、中をのぞき込む。仕事終わりの少し疲れた雰囲気。自然光。 |
| **C2** | 3–6s | 冷蔵庫の中身のインサート（日本のスーパーで買える食材が**バランスよく**並ぶ：肉・魚・野菜）。続けて彼女の思案顔のクローズアップ。「何を作ろう…」と考え込む。 |
| **C3** | 6–8s | 小さくため息。少し眉をひそめる／こめかみに手をやる等、「毎日これを考えるのが大変」という認知負荷が伝わる表情。 |
| **C4** | 8–10s | 手元のスマホ画面に献立候補が3つ“ポン”と並ぶ。彼女の表情がふっと明るくなる（悩み→解放の対比）。 |

> 編集メモ: C1–C3 は「痛み（考える辛さ）」、C4 は「解決の予感」。ナレーションはこの映像には**乗せない**（0–10sは無音、テロップとSEのみ）。ナレーション開始は 0:10 のタイトルカード以降。

---

## 冷蔵庫の中身（食材リスト・日本のスーパー基準）

C2 のインサートで見える食材は、**日本のスーパーで普通に手に入るもの**を肉・魚・野菜バランスよく。特別な高級食材や外国の食材は出さない。

- **肉**: 鶏もも肉／豚こま切れ肉（パック入り）
- **魚**: 鮭の切り身／さんま 等（パックまたはトレー）
- **野菜**: キャベツ・玉ねぎ・にんじん・長ねぎ・きのこ（しめじ）・トマト・卵
- **その他**: 豆腐・牛乳・調味料の瓶 等の生活感のある常備品

> ゴロゴロと大量に詰め込むのではなく、**普通の家庭の“ほどほどに入った”冷蔵庫**の生活感を出す。

---

## プロンプト（Seedance 2.0に入力）

### 推奨プロンプト（日本語 / 通し10秒）

```
日本の家庭のキッチン。30歳前後の清潔感のある美しい主婦が一人で、仕事終わりの少し疲れた様子で冷蔵庫のドアを開ける。中をのぞき込み「今日は何を作ろう」と小さく独り言。冷蔵庫を見ながら途方に暮れた表情。冷蔵庫の中には日本のスーパーで買える食材がバランスよく入っている（鶏肉、豚肉、鮭の切り身、キャベツ、玉ねぎ、にんじん、卵、豆腐、牛乳など）。彼女の思案する顔のクローズアップ。小さくため息をつき、毎日献立を考える負担が伝わる表情。キッチンカウンターのスマホに通知が届く。画面が光る。スマホを見ると、画面に献立カードの候補が3つ表示され、彼女の表情がふっと明るくなる。自然光、リアルな生活感、映画的で優しいトーン。10秒。
```

### 英語プロンプト（モデルによっては英語の方が精度高）

```
A Japanese home kitchen. A clean, beautiful housewife in her early 30s, alone, looking slightly tired after work, opens the refrigerator door. She looks inside, thinking "what should I cook today", with a pensive expression. The fridge holds a balanced selection of everyday Japanese-supermarket ingredients (chicken, pork, a fillet of salmon, cabbage, onion, carrot, eggs, tofu, milk). Close-up of her thoughtful face. She sighs quietly, conveying the daily mental burden of deciding meals. Finally, three meal suggestions appear on her smartphone screen and her expression brightens with relief. Natural light, realistic domestic feel, cinematic and gentle tone. 10 seconds.
```

### ネガティブプロンプト

```
animation, cartoon, illustration, text, subtitle, title, logo, bright artificial light, restaurant, supermarket, crowded overstuffed refrigerator, children, family, multiple people, men, western food, foreign ingredients, luxury ingredients
```

---

## Seedance設定

| パラメータ | 推奨値 |
|---|---|
| Duration | 10s（分割生成する場合は C1–C2 を5s、C3–C4 を5sで2本生成し編集で連結） |
| Aspect Ratio | 16:9 |
| Motion | Low–Medium（カメラが急に動かないように） |
| Seed | 任意（気に入ったものが出るまで複数生成） |

> **分割生成のすすめ**: 10秒を一発で破綻なく出すのは難しいため、**C1–C2（開ける〜中身〜思案）5秒** と **C3–C4（ため息〜スマホに3案）5秒** の2本に分けて生成し、編集で連結する方が安定する。特に C4 のスマホ画面は実機の3案スクショを後から合成しても良い。

---

## 使い方（生成後）

1. 出力をダウンロードして `video-project/public/assets/seedance_hook.mp4` に配置
2. `scripts/record/final_render.sh` を実行すると自動で冒頭に結合される

### Remotion側での使い方（冒頭のみSeedance使用）

`video-project/src/Root.tsx` の `VIDEOS.seedance` を設定:

```typescript
const VIDEOS = {
  seedance: "assets/seedance_hook.mp4",  // ← この行を有効化
  // ...
};
```

---

## 代替案（Seedanceが使えない場合）

Remotionの S1Hook コンポーネントには **冷蔵庫素材なしのフォールバック**が実装済み。
冷蔵庫アイコン（🧊）と黒背景で代替表示されるため、Seedance素材がなくてもデモは成立する。

フリー素材の代替:
- [Pexels: refrigerator opening](https://www.pexels.com/search/videos/refrigerator/) ※ライセンス確認必須
- [Pixabay: fridge door](https://pixabay.com/videos/search/fridge/)

---

## 完成チェックリスト

- [ ] 映像が10秒（4カットのカット点で意図通り展開する）
- [ ] 登場人物は主婦1名のみ（家族・子供・複数人が映り込んでいない）
- [ ] 冷蔵庫の中身が日本のスーパー基準（肉・魚・野菜バランスよく／過剰に詰め込まない）
- [ ] 「考える辛さ（逡巡）→ スマホに3案で解放」の対比が映像だけで伝わる
- [ ] 音声なし（映像トラックのみ）
- [ ] `video-project/public/assets/seedance_hook.mp4` に配置済み
- [ ] `final_render.sh` で自動結合されることを確認
