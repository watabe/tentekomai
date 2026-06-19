# tentekom.ai — キーワード入力型レポート生成AIエージェント

🌀 **tentekom.ai**（「てんてこ舞い」がモチーフ）は、キーワードを与えると
ローカル LLM（**LM Studio / llama.cpp** などの OpenAI 互換 API）で
情報収集 → 構成 → 生成 → 品質チェック → **HTML 出力**までを自動化する CLI ツールです。

設計は [要件定義](tmp/prompt_result.md) に準拠し、ローカル LLM で起きやすい
「出力切れ・重複・構成崩れ」を避けるため **パイプライン型 + 分割生成 + 中間保存 + 再開** を採用しています。

## 2つのレポート形式

`--type` によって生成パスが切り替わります。

| 形式 | `--type` | 中身 |
| --- | --- | --- |
| **ニュースダイジェスト型** | `news` | 多数のニュースをカテゴリ別に列挙。各ニュースは「サムネ画像＋見出し＋2〜3文要約＋出典リンク」。具体的なニュースをたくさん見せたいとき向け |
| **エッセイ（調査）型** | `research` / `comparison` / `proposal` / `tech` | アウトライン→章ごと長文→品質チェック→統合。根拠付きの読み物レポート |

## 特徴

- **OpenAI 互換 API に接続**（接続先は起動時に `--provider` で選択、または `--base-url` で直接指定）
- **SearXNG で情報収集**（テキスト＋画像）
- **ニュースダイジェスト**：検索クエリをカテゴリにして多数のニュースを列挙。各記事の **og:image をサムネ**として表示（無ければ favicon にフォールバック）。**鮮度フィルタ**（既定で直近1ヶ月、`--time-range` で調整）と、`--audience consumer`/`beginner` 時の**柔らかい文体**に対応。news では報道メディアを優先し、古い政府・調査PDFは降格
- **エッセイ型**：章ごと生成（800〜1,500字単位）＋ `finish_reason=length` の**出力切れ検知と継続生成**＋品質チェック（出力切れ / 文字数 / 根拠引用 / 重複 / 事実性）
- **スマホ向け PDF 出力**（`--format pdf` / `all`）：画像を base64 で**焼き込み**、**全体を1枚の縦長ページ**（スクロール閲覧でき、ページ分割の余白が出ない）で生成。スマホでローカル HTML を開くと外部画像を取得できない問題を回避できます（画像は幅いっぱいに縮小埋め込み、PDF は数 MB 程度。内容が極端に長い場合のみ自動でページ分割にフォールバック）
- **思考（reasoning）型モデル対応**：`reasoning_content` 分離・`<think>` 除去・部分 JSON 救出・トークン上限を大きめに設定
- **中間成果物を全保存**し、失敗時は `resume` で**ステップ／章単位で再開**
- LLM 呼び出しログ（prompt / response / token / 時間）を JSONL で記録
- 生成 HTML に **tentekom.ai のロゴ（回転ピンウィール）とファビコン**を埋め込み

## セットアップ

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # requests / PyYAML / markdown / weasyprint / Pillow
```

> PDF 出力に使う **weasyprint** は環境によっては system ライブラリ（pango / cairo など）が必要です。
> macOS は `brew install pango`、Linux は各ディストリの weasyprint ドキュメントを参照してください。
> PDF が不要なら `--format html`（既定）のままで weasyprint は使われません。

LLM サーバ（いずれか）を起動しておきます：

- **LM Studio**: ローカルサーバを起動 → `http://localhost:1234/v1`
- **llama.cpp**: `llama-server -m model.gguf` → `http://127.0.0.1:8080/v1`

情報収集には **SearXNG** インスタンスが必要です。**JSON 出力はデフォルト無効**なので、
`settings.yml` の `search.formats` に `json` を追加して再起動してください（Docker なら
`/etc/searxng/settings.yml` を編集 → `docker restart searxng`）。

```yaml
search:
  formats:
    - html
    - json
```

> **モデル選びの注意（低スペック環境）**：本ツールは1レポートで多数の LLM 呼び出しを行います。
> 27B 級の密モデルや思考が長いモデルは遅く不安定になりがちです。検証環境では
> 軽量・高速な **`google/gemma-4-e4b`（約34 tok/s）** が安定でした。

## 使い方

```bash
# プロバイダ一覧
./report-agent providers

# ニュースダイジェスト（推奨フロー）
# ガジェット等の消費者向け製品ニュースは --audience consumer を付けると
# 検索クエリが製品・ブランド寄り(スマートグラス/iPhone/Galaxy 等)になります。
./report-agent run "2026年6月 ガジェット ニュース 新製品" \
    --provider lmstudio \
    --model "google/gemma-4-e4b" \
    --type news --audience consumer \
    --searxng-url http://localhost:8080

# エッセイ（調査レポート）を生成
./report-agent run "ローカルLLM エージェント 比較" \
    --provider lmstudio --model "google/gemma-4-e4b" \
    --type research --length 5000 \
    --searxng-url http://localhost:8080

# 検索なし（LLM の知識のみ）
./report-agent run "2026年 生成AI トレンド" --provider lmstudio --model "google/gemma-4-e4b" --no-search

# 任意の互換エンドポイントを直接指定
./report-agent run "キーワード" --base-url http://192.168.1.10:8080/v1 --model my-model

# 途中で失敗したら再開（完了済みステップ／章はスキップ）
./report-agent resume reports/20260617_xxxxx
```

設定ファイルを使う場合は [`config.yaml.example`](config.yaml.example) をコピーして編集し、
`--config config.yaml` を渡します。**優先順位は CLI 引数 > 設定ファイル > プリセット既定値**。
`resume` は保存済み設定（`input.yaml`）を使うため、既定値を変えたいときは新規 `run` してください。

### 主なオプション（`run`）

| オプション | 説明 |
| --- | --- |
| `--provider` | `llama.cpp` / `lmstudio` / `custom` |
| `--base-url` | 互換 API の base_url（`--provider` より優先） |
| `--model` | モデル名（例 `google/gemma-4-e4b`） |
| `--type` | `news`（ダイジェスト）/ `research` / `comparison` / `proposal` / `tech` |
| `--audience` | `business` / `consumer`（一般消費者向け製品ニュース）/ `engineer` / `beginner` |
| `--length` | 目標総文字数（エッセイ型の章数の目安に使用） |
| `--format` | `html`（既定）/ `md` / `pdf` / `all` |
| `--searxng-url` | SearXNG の URL |
| `--time-range` | 鮮度フィルタ `day`/`week`/`month`/`year`/`off`（既定: news=直近1ヶ月、それ以外なし） |
| `--no-search` | 検索せず LLM の知識のみで生成 |
| `--force` | 接続確認に失敗しても続行 |

## 出力ディレクトリ

```
reports/<YYYYMMDD_キーワード>/
  input.yaml          実行パラメータ＋設定スナップショット
  state.json          完了ステップ（resume 用）
  research/
    plan.json         調査計画・検索クエリ
    sources.json      情報源（title/url/取得日時/信頼度/カテゴリ/サムネ）
    images.json       画像参照（ギャラリー用）
  outline.json        アウトライン（エッセイ型）
  chapters/           章本文とメタ（エッセイ型）
  digest.json         ニュース項目（news 型）
  review/issues.json  品質チェック結果（エッセイ型）
  output/
    report.md  report.html  report.pdf   # pdf は画像埋め込み・スマホ向け
  logs/llm_calls.jsonl  LLM 呼び出しログ
```

## アーキテクチャ

```
CLI ─ Pipeline ─┬ Planner    キーワード→調査方針・検索クエリ
                ├ Researcher SearXNG で収集（テキスト/画像）
                │
                ├─[news]→ Digest    クエリ別に分類 → 各ニュースを要約
                │                    + og:image サムネ取得（並列）
                │
                └─[essay]→ Outliner → Writer → Reviewer
                                      （出力切れは継続生成 / 品質チェック）
                ↓
                Exporter   Markdown / HTML（ロゴ・画像・サムネ埋め込み）
```

単一プロセス内のステップ分割で、各ステップが中間成果物を保存します。

## テスト

LLM／検索サーバなしで全パイプライン（エッセイ型・ニュースダイジェスト型）を検証できます：

```bash
.venv/bin/python tests/test_offline.py
```

## ロードマップ

- ✅ CLI + SearXNG + HTML（ニュースダイジェスト＋サムネ／エッセイ／ロゴ）
- PDF 出力
- 検索の鮮度フィルタ（`time_range`）強化
- 定期実行 / 過去レポート RAG
