## 要件定義案：キーワード入力型レポート生成AIエージェント

### 1. 目的

与えられたキーワードをもとに、情報収集・構成設計・章ごとの本文生成・品質チェック・最終レポート出力までを自動化する。

接続先は **llama.cpp / LM Studio のOpenAI互換API** を前提にする。llama.cpp serverはOpenAI互換のchat completions等を持ち、LM StudioもOpenAI互換APIを提供しているため、接続層は共通化できる。([GitHub][1]) ([LM Studio][2])

---

## 2. 基本コンセプト

作るべきは、いきなり1回で長文レポートを書くエージェントではなく、以下のパイプライン型。

```text
キーワード入力
  ↓
意図解釈
  ↓
検索クエリ生成
  ↓
情報収集
  ↓
素材整理
  ↓
アウトライン作成
  ↓
章ごと生成
  ↓
章ごと検証
  ↓
全体統合
  ↓
最終レビュー
  ↓
Markdown / PDF / HTML 出力
```

理由は、ローカルLLMは長文一括生成で
**コンテキスト肥大・出力途中切れ・幻覚・重複・構成崩れ** が起きやすいためです。

---

## 3. 機能要件

### A. 入力要件

最低限はこれ。

| 項目     | 内容                                  |
| ------ | ----------------------------------- |
| キーワード  | 例：「2026年 ガジェットニュース」「生成AI エージェント比較」  |
| レポート種別 | ニュースまとめ / 調査レポート / 比較表 / 提案書 / 技術調査 |
| 文字数    | 例：3,000字 / 8,000字 / 章ごと1,000字       |
| 出力形式   | Markdown / HTML / PDF               |
| 情報鮮度   | 最新重視 / 定番情報重視 / 指定期間                |
| 文体     | ビジネス向け / 技術者向け / 初心者向け              |

最初はCLIで十分です。

```bash
report-agent "ローカルLLM エージェント 比較" --type research --length 5000 --format md
```

---

### B. 情報収集要件

情報源は段階的に増やす。

| 優先度     | 情報源                | 備考          |
| ------- | ------------------ | ----------- |
| Phase 1 | Web検索API / SearXNG | まずこれ        |
| Phase 2 | RSS / ニュースサイト      | 定期レポート向き    |
| Phase 3 | ローカルファイル           | 社内資料・過去レポート |
| Phase 4 | RAG / ベクトルDB       | 長期運用時       |

要件としては、**必ずURL・タイトル・取得日時を保存**する。

```json
{
  "title": "...",
  "url": "...",
  "published_at": "...",
  "retrieved_at": "...",
  "summary": "...",
  "reliability_score": 0.8
}
```

---

### C. レポート生成要件

一括生成は禁止。章ごとに生成する。

```text
1. 全体アウトライン生成
2. 各章の目的を定義
3. 各章に必要な素材を割り当て
4. 章ごとに本文生成
5. 章ごとに自己レビュー
6. 最後に全体を統合
```

章ごとの生成単位は、ローカルLLMなら **800〜1,500字程度** が安全です。

---

### D. 品質チェック要件

最低限、以下のチェックを入れる。

| チェック    | 内容              |
| ------- | --------------- |
| 根拠チェック  | 主張に対応する参照元があるか  |
| 重複チェック  | 同じ話を何度もしていないか   |
| 構成チェック  | 見出しと本文が対応しているか  |
| 文字数チェック | 指定文字数に近いか       |
| 事実性チェック | 数値・固有名詞・日付を重点確認 |
| 出力切れ検知  | 文末が不自然に途切れていないか |

特に重要なのは **出力切れ検知** です。Hermesで起きていた `finish_reason='length'` 系の事故を防げます。

---

## 4. 非機能要件

### A. LLM接続要件

llama.cpp / LM Studio / OpenAI API を同じインターフェースで扱う。

```yaml
llm:
  provider: openai_compatible
  base_url: http://127.0.0.1:8080/v1
  model: qwen3.6-35b
  temperature: 0.3
  max_tokens: 1500
  timeout_sec: 300
```

LM Studioは通常 `http://localhost:1234/v1` をbase URLにしてOpenAIクライアントから利用できる。([LM Studio][2])

---

### B. 安定性要件

| 項目       | 要件                                   |
| -------- | ------------------------------------ |
| リトライ     | タイムアウト・JSON崩れ・出力切れ時に再実行              |
| 中間保存     | 各章・素材・アウトラインをファイル保存                  |
| 再開機能     | 途中失敗しても章単位で再開可能                      |
| ログ       | prompt / response / token数 / 処理時間を保存 |
| 最大反復数    | 無限ループ防止。例：1章3回まで                     |
| コンテキスト制御 | 必要な素材だけ章に渡す                          |

ここが最重要です。
「自律性」より **途中保存・再開・章分割** を優先すべきです。

---

## 5. 推奨アーキテクチャ

```text
CLI / Web UI
  ↓
Job Manager
  ↓
Planner Agent
  ↓
Research Agent
  ↓
Outline Agent
  ↓
Writer Agent
  ↓
Reviewer Agent
  ↓
Exporter
```

### 各Agentの責務

| Agent      | 役割                 |
| ---------- | ------------------ |
| Planner    | キーワードから調査方針を決める    |
| Researcher | 検索・取得・要約           |
| Outliner   | レポート構成を作る          |
| Writer     | 章ごとに本文を書く          |
| Reviewer   | 品質チェック・修正指示        |
| Exporter   | Markdown/PDF/HTML化 |

ただし実装上は、最初からマルチエージェントにしなくてよいです。
**単一プロセス内のステップ分割**で十分です。

---

## 6. データ保存要件

ディレクトリはこうするのが扱いやすいです。

```text
reports/
  20260616_local_llm_agent/
    input.yaml
    research/
      sources.json
      notes.md
    outline.md
    chapters/
      01_intro.md
      02_market.md
      03_comparison.md
    review/
      issues.json
      final_check.md
    output/
      report.md
      report.html
      report.pdf
    logs/
      llm_calls.jsonl
```

これにより、失敗時に

```bash
report-agent resume reports/20260616_local_llm_agent
```

で再開できます。

---

## 7. MVP要件

最初のMVPはここまででよいです。

| 区分 | 要件               |
| -- | ---------------- |
| 入力 | キーワード、文字数、出力形式   |
| 検索 | SearXNG or Web検索 |
| 生成 | Markdownレポート     |
| 分割 | アウトライン→章ごと生成     |
| 保存 | 中間ファイル保存         |
| 接続 | OpenAI互換API      |
| 品質 | 出力切れ・重複・根拠不足チェック |
| 再開 | 章単位のresume       |

逆に、初期MVPで不要なもの。

| 不要           | 理由               |
| ------------ | ---------------- |
| 完全自律ブラウザ操作   | 重い・不安定           |
| Docker前提     | OpenHands的な重さが出る |
| 複雑なマルチエージェント | デバッグ困難           |
| ベクトルDB       | 最初は過剰            |
| GUI          | CLIで十分           |

---

## 8. 重要な設計方針

### 方針1：LLMには「書かせる」だけに寄せる

検索、保存、再試行、文字数管理、章管理はPython側で制御する。
LLMに全部任せるとHermesと同じく長時間化・暴走しやすいです。

---

### 方針2：プロンプトは短く、役割を固定する

悪い例：

```text
検索して、考えて、構成して、レポートを書いて、最後にまとめて
```

良い例：

```text
あなたの役割は第2章の本文生成のみです。
以下のアウトラインと素材だけを使い、1,000字以内で書いてください。
出典にない事実は書かないでください。
```

---

### 方針3：章ごとにmax_tokensを小さくする

ローカルLLMでは、1回の出力を大きくしすぎない。

推奨：

```yaml
generation:
  chapter_max_tokens: 1200
  review_max_tokens: 600
  final_max_tokens: 1000
```

---

## 9. 最終的な要件定義サマリ

このAIエージェントの要件はこう定義するのがよいです。

> キーワードを入力すると、外部情報を収集・整理し、アウトラインを作成したうえで、章ごとに根拠付きレポートを生成する。llama.cpp / LM Studio などのOpenAI互換APIに接続でき、生成途中の失敗に備えて中間成果物を保存し、章単位で再開できる。出力はMarkdownを標準とし、必要に応じてHTML/PDFへ変換する。

---

## 10. 次に決めるべきこと

次はこの3つを決めると実装に進めます。

1. **CLIツールにするか、Web UIにするか**
2. **検索元をSearXNGにするか、Web検索APIにするか**
3. **レポート形式をニュースまとめ型にするか、調査レポート型にするか**

おすすめは、

```text
Phase 1：CLI + SearXNG + Markdown
Phase 2：PDF出力
Phase 3：定期実行
Phase 4：過去レポートRAG
```

です。

[1]: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md?utm_source=chatgpt.com "llama.cpp/tools/server/README.md at master · ggml-org ..."
[2]: https://lmstudio.ai/docs/developer/openai-compat?utm_source=chatgpt.com "OpenAI Compatibility Endpoints"
