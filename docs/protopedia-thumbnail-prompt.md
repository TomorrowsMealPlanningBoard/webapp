# Proto Pedia サムネイル画像 生成指示書（Gemini / Nano Banana）

Proto Pedia の **作品一覧に並ぶサムネイル**用の1枚絵。TomorrowsMeal を「1枚で説明できる」ことを最優先に設計する。

> **狙い**: 一覧のカードでパッと見た瞬間に「冷蔵庫の写真 → AIが今日の献立を提案してくれるアプリ」だと伝わること。文字が読めない小サイズでも成立する構図にする。世界観は CLAUDE.md のブランド（優しい・清潔感・エメラルド・料理のワクワク感）に厳密に合わせる。

---

## 1. 前提（Gemini 画像生成のベストプラクティス要約）

公式ガイド（DeepMind / Google Cloud）の要点を本プロンプトに反映済み：

- **5要素の型**で書く: **Style（画風）→ Subject（主役）→ Setting（場所）→ Action（動作）→ Composition（構図・アングル・アスペクト比）**。
- **キーワードの羅列より、情景を叙述する文章**で書くほど精度が上がる（「detail drives results」）。
- フォトリアルには**撮影用語を1つ以上**足す: レンズ（`50mm` / `85mm`）、ライティング（`soft natural window light`, `golden hour`）、リアリズムのトリガー（`photorealistic`, `cinematic realism`）。
- **アスペクト比を明示**する。サムネなので **1:1（正方形）を基本**とし、横長が必要なら 4:3 / 16:9 も生成しておく。
- **画像内の文字は使わない**（多言語・小サイズで破綻するため）。どうしても入れる場合のみ、入れたい語を引用符で囲みタイポを指定する（本指示では原則ロゴ文字なし）。
- **1回で完璧を狙わず**、バリエーションを複数生成して選ぶ（「three distinct variations」等）。

推奨モデル: **Gemini 3.x / Nano Banana Pro**（`gemini-3-pro-image` 系）。解像度は 2K 以上で生成し、サムネにリサイズ。

---

## 2. 画づくりの設計（1枚で何を見せるか）

TomorrowsMeal のコアは「**冷蔵庫を撮るだけ → 4体のAIエージェントが協調 → 朝昼晩3案が返る**」。これを1枚に凝縮する。

**採用コンセプト（メイン）: "冷蔵庫 → スマホの3案" のビフォーアフターを1フレームに**

- 中央〜右に、**手に持ったスマホ**。画面に**献立カードが縦に3枚（朝・昼・晩）**並ぶ。カードには料理のサムネと簡単な要素（読めなくてよい／ダミーの塗りで可）。
- 背景に、少しボケた**開いた家庭用冷蔵庫**（日本のスーパーの食材がほどほどに入っている）。「冷蔵庫の中身 → スマホの提案」という因果が視線で追える配置。
- 全体は**エメラルド〜フレッシュグリーン**を基調に、清潔な白背景。優しく親しみやすいトーン。
- **AIエージェント感**は、スマホから料理カードへ向かう淡い光の粒子／やわらかなグリーンのグロー等で「AIが提案している」ニュアンスを最小限に添える（過剰なSF演出は不可）。

> 補助コンセプト（サブ案）は §5 に別プロンプトとして用意。まずメインを複数生成して選ぶ。

---

## 3. 要件

| 項目 | 内容 |
|---|---|
| 用途 | Proto Pedia 作品一覧のサムネイル（小サイズで一覧表示される） |
| アスペクト比 | **1:1（正方形）を本命**。予備で 4:3 と 16:9 も生成 |
| 解像度 | 2K 以上で生成 → サムネにリサイズ |
| 画風 | フォトリアル（実写ベース）＋ 清潔で明るいプロダクト写真調。アニメ/イラスト/3D CG調は不可 |
| 文字 | **画像内に文字・ロゴを入れない**（小サイズ・多言語で破綻するため） |
| 人物 | 原則なし（手だけはOK）。顔出しの人物は入れない |
| トーン | 優しい・清潔感・料理のワクワク感。エメラルド基調 |
| NG | ケバい人工照明、レストラン/スーパーの業務感、過剰にSFなUI、食材の詰め込みすぎ、乱雑さ |

---

## 4. プロンプト（メイン案 / Gemini に入力）

### 4-1. 英語（推奨・精度優先）

```
A clean, bright product photograph in a gentle, friendly mood. Photorealistic, cinematic realism, shot on 50mm, soft natural window light, shallow depth of field.

Subject: a hand holding a modern smartphone in the center-right of the frame. On the phone screen, three neat vertical meal-suggestion cards are stacked (breakfast, lunch, dinner), each card showing an appetizing home-cooked Japanese dish thumbnail in a soft rounded UI. The app UI uses a fresh emerald-green accent color on a clean off-white background.

Setting: softly blurred in the background, an open household refrigerator with a modest, tidy selection of everyday Japanese-supermarket ingredients (vegetables, eggs, tofu, a fillet of fish, some meat). The scene reads as "the fridge on the left leads to meal suggestions on the phone".

Action: a subtle, soft stream of tiny glowing green particles gently flows from the fridge toward the phone screen, suggesting an AI quietly turning ingredients into meal ideas. Keep this effect minimal and elegant, not sci-fi.

Composition: square 1:1 aspect ratio, centered balanced composition, the phone sharp and in focus, the fridge softly out of focus, bright clean emerald-and-white palette, plenty of soft light, shallow depth of field. No text, no logos, no letters anywhere in the image. No human face, only a hand.
```

### 4-2. 日本語（英語で崩れる場合の代替）

```
清潔で明るいプロダクト写真。優しく親しみやすい雰囲気。フォトリアル、シネマティックなリアリズム、50mm、柔らかい自然光、浅い被写界深度。

主役: フレーム中央やや右で、手がスマートフォンを持っている。スマホ画面には、朝・昼・晩の献立提案カードが縦に3枚きれいに並び、それぞれに美味しそうな日本の家庭料理のサムネイルが表示されている。アプリUIは清潔なオフホワイト背景に、フレッシュなエメラルドグリーンのアクセントカラー。

場所: 背景に、少しぼけた家庭用冷蔵庫が開いていて、日本のスーパーで買える食材がほどほどに整然と入っている（野菜、卵、豆腐、魚の切り身、少しの肉）。「左の冷蔵庫 → スマホの献立提案」へと視線が流れる構図。

動作: 冷蔵庫からスマホ画面に向かって、ごく淡いグリーンの光の粒子がやわらかく流れ、AIが食材から献立を考えている様子をさりげなく表現する。効果は最小限で上品に。SFにはしない。

構図: 1:1の正方形。バランスの取れた中央構図。スマホにピント、冷蔵庫は柔らかくボケる。明るく清潔なエメラルド＆ホワイトの配色、浅い被写界深度。画像内に文字・ロゴ・文字列は一切入れない。人の顔は出さず、手のみ。
```

### 4-3. ネガティブプロンプト（対応モデルのみ）

```
text, letters, words, logo, watermark, subtitle, UI mockup with readable text, anime, cartoon, illustration, 3d render, cgi, sci-fi hologram, neon, harsh artificial lighting, restaurant kitchen, supermarket, cluttered overstuffed refrigerator, messy, low quality, human face, multiple people, distorted hands
```

---

## 5. サブ案（一覧で映えるバリエーション）

好みで生成・比較する。§4 と同じ画風・配色・「文字なし」ルールを引き継ぐこと。

### サブ案A: フラットレイ（俯瞰・アイコン的で一覧映えする）

```
A top-down flat-lay product photograph, photorealistic, bright clean studio light, emerald-and-white palette, square 1:1.

On a clean off-white table: on the left side, a smartphone showing three stacked meal-suggestion cards (breakfast, lunch, dinner) with appetizing Japanese home dishes in a soft emerald-accented app UI; on the right, a tidy arrangement of fresh everyday ingredients (vegetables, egg, tofu, fish, herbs) as if just taken from a fridge. A soft green arrow-like flow of light connects the ingredients to the phone. Balanced, minimal, gentle and appetizing. No text, no logos anywhere.
```

### サブ案B: 冷蔵庫の"のぞき込み"主役（プロダクトの入口を強調）

```
A photorealistic, cozy, bright photograph, 35mm, soft natural light, square 1:1, emerald-and-white gentle palette.

An open household refrigerator seen from the front, holding a modest tidy selection of everyday Japanese-supermarket ingredients. In the foreground, a hand holds up a smartphone toward the fridge; the phone screen shows three neat meal-suggestion cards (breakfast, lunch, dinner) with appetizing dishes in a clean emerald-accented UI, as if the app just read the fridge and suggested meals. Warm friendly domestic mood, shallow depth of field. No text, no logos, no human face.
```

---

## 6. 仕上げ・チェックリスト

- [ ] **1:1 正方形**で最低3バリエーション生成し、一覧サムネ相当（小サイズ）に縮小して視認性を確認した
- [ ] 小サイズでも「スマホの3案の献立」と「冷蔵庫（食材）」の関係が一目で伝わる
- [ ] エメラルド＋清潔な白基調で、ブランドの世界観（優しい・清潔・ワクワク）に合っている
- [ ] **画像内に文字・ロゴが写り込んでいない**（多言語・小サイズ対策）
- [ ] アニメ/CG調でなくフォトリアルになっている
- [ ] 人物の顔・複数人・乱雑な詰め込み冷蔵庫が写っていない
- [ ] AI表現（光の粒子等）が過剰なSFになっておらず、上品な最小限に収まっている
- [ ] 予備で 4:3 / 16:9 版も1枚ずつ生成した（他媒体流用のため）

---

## 参考（Gemini 画像生成ベストプラクティス出典）

- [Nano Banana Prompt Guide — Google DeepMind](https://deepmind.google/models/gemini-image/prompt-guide/)
- [Ultimate prompting guide for Nano Banana — Google Cloud Blog](https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-nano-banana)
- [Prompting tips for Nano Banana Pro — Google Blog](https://blog.google/products-and-platforms/products/gemini/prompting-tips-nano-banana-pro/)
