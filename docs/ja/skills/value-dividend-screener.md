---
layout: default
title: "Value Dividend Screener"
grand_parent: 日本語
parent: スキルガイド
nav_order: 44
lang_peer: /en/skills/value-dividend-screener/
permalink: /ja/skills/value-dividend-screener/
---

# Value Dividend Screener
{: .no_toc }

バリュー特性（PER 20倍以下、PBR 2倍以下）、魅力的な利回り（3%以上）、安定した成長（配当/売上/EPSが3年間上昇トレンド）を組み合わせて、高品質な配当銘柄をスクリーニングするスキルです。ローカルTradingViewデータレイヤーを使用（APIキー不要）。任意のFINVIZ Eliteプレスクリーンで対象ユニバースをS&P 500以外にも拡大できます。配当株スクリーニング、インカムポートフォリオのアイデア、ファンダメンタルズの優れたバリュー銘柄の検索時に使用します。
{: .fs-6 .fw-300 }

<span class="badge badge-free">APIキー不要</span> <span class="badge badge-optional">FINVIZ任意</span>

[スキルパッケージをダウンロード (.skill)](https://github.com/tradermonty/claude-trading-skills/raw/main/skill-packages/value-dividend-screener.skill){: .btn .btn-primary .fs-5 .mb-4 .mb-md-0 .mr-2 }
[GitHubでソースを見る](https://github.com/tradermonty/claude-trading-skills/tree/main/skills/value-dividend-screener){: .btn .fs-5 .mb-4 .mb-md-0 }

<details open markdown="block">
  <summary>目次</summary>
  {: .text-delta }
- TOC
{:toc}
</details>

---

## 1. 概要

バリュー特性、魅力的なインカム、安定した成長を兼ね備えた高品質配当銘柄を特定するスキルです。

2つのスクリーニングモード：

1. **TradingViewデータレイヤー（デフォルト）**: S&P 500ユニバースを走査し、ファンダメンタルズ、年次DPS履歴、日足バーを稼働中のTradingView Desktopチャートから共有 `tv_client` データレイヤー経由で取得 — **APIキー不要・リクエスト上限なし**。
2. **FINVIZ Eliteプレスクリーン（任意）**: FINVIZ Elite APIの1回の呼び出しで米国市場全体（ミッドキャップ以上）をバリュー/配当条件でプレフィルタし、その後TradingViewが詳細分析を提供。ユニバースをS&P 500以外にも拡大。

バリュエーション比率、配当指標、財務健全性、収益性などの定量基準に基づいて米国株をスクリーニングし、複合品質スコアによるランキングレポートを生成します。最終ランキングでは売られ過ぎ銘柄（RSI ≤ 40）が優先されます。

---

## 2. 使用タイミング

以下のリクエストがあった場合にこのスキルを使用してください：
- 「高品質な配当銘柄を探して」
- 「バリュー配当銘柄のスクリーニングを実行」
- 「配当成長率の高い銘柄を見せて」
- 「適正なバリュエーションのインカム銘柄を探して」
- 「持続可能な高利回り銘柄をスクリーニング」
- 配当利回り、バリュエーション指標、ファンダメンタル分析を組み合わせたリクエスト全般

---

## 3. 前提条件

- **TradingViewデータレイヤー** 必須: 稼働中のTradingView Desktopチャート（CDP :9222）または新鮮な `state/metrics` スナップショットキャッシュ — **APIキー不要・リクエスト上限なし**
- **FINVIZ Elite** 任意（ユニバースをS&P 500以外に拡大）
- Python 3.9+ 推奨。`requests` はFINVIZプレスクリーン使用時のみ必要
- レガシーの `FMP_API_KEY` / `--fmp-api-key` 入力は受け付けるが無視される

---

## 4. クイックスタート

```bash
# S&P 500ユニバース via TradingView（デフォルト、APIキー不要）
python3 skills/value-dividend-screener/scripts/screen_dividend_stocks.py

# FINVIZプレスクリーンによる2段階スクリーニング（より広いユニバース）
python3 skills/value-dividend-screener/scripts/screen_dividend_stocks.py --use-finviz

# カスタムパラメータ
python3 skills/value-dividend-screener/scripts/screen_dividend_stocks.py \
  --top 50 \
  --output-dir reports/
```

---

## 5. ワークフロー

### ステップ1: ユニバースの選択

#### S&P 500 via TradingView（デフォルト）

稼働中のTradingView Desktopチャート以外のセットアップは不要。スクリーナーはコミット済みのS&P 500構成銘柄リストを走査し、すべてのデータ（ファンダメンタルズ、年次DPS履歴、日足バー）をTradingViewスキャナーから読み取ります。

#### FINVIZプレスクリーン（任意、より広いユニバース）

```bash
export FINVIZ_API_KEY=your_finviz_key_here
```

**FINVIZを使う理由：**
- 1回のAPI呼び出しで米国市場全体（ミッドキャップ以上）をバリュー/配当条件でプレスクリーニング
- その後TradingViewがプレスクリーン済み候補の詳細分析を提供

**FINVIZ Elite APIキー：**
- FINVIZ Eliteサブスクリプションが必要（月額約$40または年額約$330）
- プレスクリーニング結果のCSVエクスポートへのアクセスを提供

### ステップ2: スクリーニングの実行

**デフォルトのS&P 500スクリーニング：**

```bash
python3 skills/value-dividend-screener/scripts/screen_dividend_stocks.py
```

**2段階スクリーニング（FINVIZ + TradingView）：**

```bash
python3 skills/value-dividend-screener/scripts/screen_dividend_stocks.py --use-finviz
```

FINVIZフィルタ（1回の呼び出し）: 時価総額ミッドキャップ以上、配当利回り3%以上、配当成長率（3年）5%以上、EPS成長率（3年）プラス、PBR 2倍以下、PER 20倍以下、売上成長率（3年）プラス、米国。

**カスタム上位N件 / 出力先 / 候補数上限：**

```bash
python3 skills/value-dividend-screener/scripts/screen_dividend_stocks.py \
  --top 50 --output-dir reports/ --max-candidates 200
```

**スクリプトの動作：**
1. ユニバース: S&P 500構成銘柄（デフォルト）またはFINVIZプレスクリーン済み銘柄（`--use-finviz`）
2. TradingView経由の銘柄ごとの詳細分析：
   - 時価総額 ≥ $2B、バリュエーションフィルタ PER ≤ 20、PBR ≤ 2（スキャナースナップショット）
   - 配当利回り ≥ 3.0%（直近完了会計年度のDPS / 現在価格で検証）
   - 配当成長率の計算（3年CAGR ≥ 4%）— スキャナーの年次DPS履歴から
   - 配当安定性チェック（ボラティリティ、連続増配年数）
   - 売上・EPSトレンド分析（年次会計年度系列、3年間プラス）
   - 配当持続可能性評価（スキャナー配当性向、DPS×株式数 / FCFカバレッジ、REITはOCF≈FFO代理）
   - 財務健全性（スナップショットの負債比率、流動比率）
   - 品質スコアリング（ROE、純利益率）
   - 日足バーから14期間RSI（最終ランキングで売られ過ぎ RSI ≤ 40 を優先）
3. 複合スコアリングとランキング
4. 上位N銘柄をJSONファイルとして `reports/` に出力

**想定実行時間：** 新鮮なメトリクスキャッシュがあればほとんどの銘柄はチャートに触れずに処理（数秒）。コールドキャッシュではライブチャート読み取りにフォールバック（プレフィルタ通過銘柄あたり約2秒）。

### ステップ3: 結果のパースと分析

生成されたJSONファイルを読み込み：

```python
import json

with open('reports/value_dividend_results_YYYY-MM-DD.json', 'r') as f:
    data = json.load(f)

metadata = data['metadata']
stocks = data['stocks']
```

**銘柄ごとの主要データ：**
- 基本情報: `symbol`, `company_name`, `sector`, `market_cap`, `price`
- バリュエーション: `dividend_yield`, `pe_ratio`, `pb_ratio`
- テクニカル: `rsi`
- 成長指標: `dividend_cagr_3y`, `revenue_cagr_3y`, `eps_cagr_3y`
- 持続可能性: `payout_ratio`, `fcf_payout_ratio`, `dividend_sustainable`
- 財務健全性: `debt_to_equity`, `current_ratio`, `financially_healthy`
- 品質: `roe`, `profit_margin`, `quality_score`
- 総合ランキング: `composite_score`

### ステップ4: Markdownレポートの生成

以下のセクションを含む構造化されたMarkdownレポートを作成：

#### レポート構成

```markdown
# Value Dividend Stock Screening Report

**Generated:** [タイムスタンプ]
**Data Source:** TradingView data layer (no API key)
**Screening Criteria:**
- Dividend Yield: >= 3.0%
- P/E Ratio: <= 20
- P/B Ratio: <= 2
- Dividend Growth (3Y CAGR): >= 4%
- Revenue Trend: Positive over 3 years
- EPS Trend: Positive over 3 years

**Total Results:** [N] stocks
```

---

## 6. リソース

**リファレンス：**

- `skills/value-dividend-screener/references/screening_methodology.md`
- `skills/value-dividend-screener/references/fmp_api_guide.md`（レガシー、参考用）

**スクリプト：**

- `skills/value-dividend-screener/scripts/screen_dividend_stocks.py`
