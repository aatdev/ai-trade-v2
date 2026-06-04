---
layout: default
title: "Dividend Growth Pullback Screener"
grand_parent: 日本語
parent: スキルガイド
nav_order: 13
lang_peer: /en/skills/dividend-growth-pullback-screener/
permalink: /ja/skills/dividend-growth-pullback-screener/
---

# Dividend Growth Pullback Screener
{: .no_toc }

年間配当成長率12%以上、利回り1.5%以上の高品質な配当成長株のうち、RSIオーバーソールド（RSI≤40）による一時的な押し目を経験している銘柄を検索するスキルです。ファンダメンタルの配当分析とテクニカルのタイミング指標を組み合わせ、短期的な弱さの中にある強い配当成長銘柄の買い機会を特定します。
{: .fs-6 .fw-300 }

<span class="badge badge-free">APIキー不要</span> <span class="badge badge-optional">FINVIZ任意</span>

[スキルパッケージをダウンロード (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/dividend-growth-pullback-screener.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[GitHubでソースを見る](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/dividend-growth-pullback-screener){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>目次</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. 概要

強いファンダメンタル特性を持ちながらも一時的なテクニカルの弱さを示している配当成長株をスクリーニングするスキルです。卓越した配当成長率（CAGR 12%以上）を持ち、RSIオーバーソールドレベル（≤40）まで押し目をつけた銘柄をターゲットにし、長期配当成長投資家のエントリー機会を創出します。

**投資テーシス:** 高品質な配当成長株（利回りは通常1〜2.5%）は、高い現在利回りではなく配当の増加を通じて資産を複利的に成長させます。これらの銘柄を一時的な押し目（RSI≤40）で購入することにより、強いファンダメンタル成長と有利なテクニカルエントリータイミングを組み合わせてトータルリターンを向上させることができます。

---

## 2. 使用タイミング

以下の場合に使用します：
- 卓越した複利ポテンシャル（配当CAGR 12%以上）を持つ配当成長株を探している場合
- 一時的な市場の弱さの中で質の高い銘柄のエントリー機会を求めている場合
- より高い配当成長のために低い現在利回り（1.5〜3%）を受け入れられる場合
- 現在のインカムよりも5〜10年のトータルリターンに注目している場合
- 市場環境がセクターローテーションや広範な押し目で質の高い銘柄に影響している場合

**以下の場合には使用しないでください:**
- 高い現在インカムを求める場合（代わりに value-dividend-screener を使用）
- 3%超の即時配当利回りが必要な場合
- 厳格なP/EやP/B要件を持つディープバリュー銘柄を探す場合
- 6ヶ月未満の短期トレードにフォーカスする場合

---

## 3. 前提条件

- **TradingViewデータレイヤー**が必要：起動中のTradingView Desktopチャート（CDP :9222）または新鮮な `state/metrics` スナップショットキャッシュ — **APIキー不要、リクエスト制限なし**
- **FINVIZ Elite** は任意（S&P 500を超えてユニバースを拡大）
- TradingViewは分析用、FINVIZはRSIプレスクリーニング用
- Python 3.9+ 推奨（`requests` はFINVIZプレスクリーン使用時のみ必要）
- 旧来の `FMP_API_KEY` / `--fmp-api-key` 入力は受け付けられますが無視されます

---

## 4. クイックスタート

```bash
# TradingView経由のS&P 500ユニバース（デフォルト、APIキー不要）
python3 skills/dividend-growth-pullback-screener/scripts/screen_dividend_growth_rsi.py

# FINVIZプレスクリーン付き2段階スクリーニング（より広いユニバース）
python3 skills/dividend-growth-pullback-screener/scripts/screen_dividend_growth_rsi.py --use-finviz

# カスタムRSI閾値と配当成長要件
python3 skills/dividend-growth-pullback-screener/scripts/screen_dividend_growth_rsi.py \
  --rsi-max 35 \
  --min-div-growth 15
```

---

## 5. ワークフロー

### ステップ1: ユニバースの選択

#### TradingView経由のS&P 500（デフォルト）

起動中のTradingView Desktopチャート以外のセットアップは不要です。スクリーナーはコミット済みのS&P 500構成銘柄リストを走査し、すべてのデータ（年間DPS履歴、ファンダメンタルズ、日足バー）をTradingViewスキャナーから読み取ります。

#### FINVIZプレスクリーン（任意、より広いユニバース）

```bash
export FINVIZ_API_KEY=your_finviz_key_here
```

**なぜFINVIZなのか？**
- 米国市場全体（ミッドキャップ以上）を配当成長＋RSIフィルターで1回のAPIコールでプレスクリーニング
- プレスクリーニング済みの約10〜50候補に対してTradingViewが詳細分析を提供

### ステップ2: スクリーニングの実行

**デフォルトのS&P 500スクリーニング:**

```bash
python3 skills/dividend-growth-pullback-screener/scripts/screen_dividend_growth_rsi.py
```

以下を実行します：
1. スキャナーの現在利回りによる軽量プレフィルター、その後年間DPSによる配当CAGR検証（12%+）
2. 日足バーから14期間RSIを計算、オーバーソールドフィルター（RSI≤40）
3. 売上・EPSトレンド、財務健全性、配当性向の持続性チェック

**2段階スクリーニング（FINVIZ + TradingView）:**

```bash
python3 skills/dividend-growth-pullback-screener/scripts/screen_dividend_growth_rsi.py --use-finviz
```

1. FINVIZプレスクリーン: 配当利回り0.5〜3%、配当成長10%+、EPS成長5%+、売上成長5%+、RSI<40
2. TradingView詳細分析: 12%+配当CAGRの検証、正確なRSI計算、ファンダメンタル分析

### ステップ3: 結果のレビュー

スクリプトは2つの出力を生成します：

1. **JSONファイル:** `dividend_growth_pullback_results_YYYY-MM-DD.json`
   - さらなる分析のためのすべてのメトリクスを含む構造化データ
   - 配当成長率、RSI値、財務健全性メトリクスを含む

2. **マークダウンレポート:** `dividend_growth_pullback_screening_YYYY-MM-DD.md`
   - 銘柄プロファイルを含む人間可読な分析
   - シナリオベースの確率評価
   - エントリータイミングの推奨

### ステップ4: 適格銘柄の分析

各適格銘柄について、レポートには以下が含まれます：

**配当成長プロファイル:**
- 現在の利回りと年間配当
- 3年配当CAGRと一貫性
- 配当性向と持続性評価

**テクニカルタイミング:**
- 現在のRSI値（≤40 = オーバーソールド）
- RSIコンテキスト（極端なオーバーソールド<30 vs 初期押し目 30-40）
- 直近トレンドに対する価格アクション

**クオリティメトリクス:**
- 売上・EPS成長（事業のモメンタム確認）
- 財務健全性（債務水準、流動性比率）
- 収益性（ROE、利益率）

**投資推奨:**
- エントリータイミング評価（即時 vs 確認待ち）
- 銘柄固有のリスク要因
- 配当成長の複利効果に基づくアップサイドシナリオ

---

## 6. リソース

**リファレンス:**

- `skills/dividend-growth-pullback-screener/references/dividend_growth_compounding.md`
- `skills/dividend-growth-pullback-screener/references/fmp_api_guide.md`
- `skills/dividend-growth-pullback-screener/references/rsi_oversold_strategy.md`

**スクリプト:**

- `skills/dividend-growth-pullback-screener/scripts/screen_dividend_growth_rsi.py`
