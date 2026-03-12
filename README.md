# MAGI: A Reproducible Evaluation Framework for LLM Research

![MAGI logo](./img/logo.png)

*MAGI — Managed Artifact Generation and Integrity*

MAGIは、LLM（大規模言語モデル）評価における**実行条件の記録・比較可能化・再実行可能化**を目的とした研究基盤です。評価結果だけでなく、評価を構成するすべての証拠（実験仕様・環境情報・出力・採点）を統一形式で保存し、再現可能な比較を可能にします。**Model Context Protocol（MCP）サーバーとして動作し**、Claude Desktop などのLLMクライアントから実験の投入・管理・結果取得を行えます。

> **このリポジトリの範囲**  
> MAGI全体のうち、**Control Plane（MCPサーバーとしても利用可能）・統合Runner・Compute Node側CLIの実装**を含みます。  
> 推論はCompute Nodeが担当し、Contol nodeでは実行しません。

---

## 背景 — なぜ実行条件の記録が必要か

LLM評価において、**同じモデルでも実行環境が異なると結果が変わる**ことが知られています。

- ハードウェア差（GPU、ドライババージョン）が出力に影響しうる
- 評価設定（温度、seed、max_tokens）の違いが再現性を損なう
- 「結果」だけを共有しても、比較の前提が揃っているか確認できない

MAGIはこの問題に対し、**評価プロセスそのものを証拠として保存する**アプローチをとります。

### Motivating Example

日本語LLM評価（DeepSeek R1系モデルなどのMoE/MLA構造を持つモデル）では、出力の言語依存性が評価精度に影響することが示唆されています。MAGIはこのような比較実験を、再現可能な形で実施するための基盤として開発されました。

---

## Design Philosophy

### Control Plane と Compute Plane の分離

```
[LLM Client (Claude Desktop / VS Code etc.)]
        ↓ SSH / VPN

======== CONTROL PLANE (MCP Serverとしても利用可能) ========
[Control node]
   ├── spec管理（実験定義）
   ├── job dispatch（ジョブ投入）
   ├── env収集（環境メタデータ）
   ├── 採点（自動評価）
   ├── 統合JSON生成
   └── アーティファクト保存
        ↓ SSH dispatch

======== COMPUTE PLANE (GPU Nodes) ========
[GPU Node(s)]  ← any ROCm / CUDA / cloud GPU node
   └── 推論実行
  ↓ responses.jsonl
  ↓ env.json
        ↓
======== BACK TO CONTROL PLANE ========
[Control node MCP]
   └── result.json 生成・保存
```

**なぜ分離するか**: Control Planeに推論を乗せると、負荷による不安定化・依存ライブラリの競合・再現性の崩壊が起きます。**単一責任の原則**：Control Planeは軽量・安定であるべきです。

---

## Architecture

### Control Plane 環境（最小要件）

> 以下は参考情報です。SSH / VPN でCompute Nodeに接続できるLinuxサーバーであれば動作します。

- **OS**: Ubuntu 22.04+ LTS
- **CPU**: x86-64（重い計算は不要）
- **RAM**: 8 GB以上推奨
- **GPU**: 不要（ディスプレイ専用でも可）— **LLM推論はここで行わない**
- **Network**: Compute Nodeへのアクセス（Tailscale / WireGuard / 直接LAN など）

### Compute Node 要件

> `nodes.yaml` に登録したSSH到達可能なGPUノードであれば何でも動作します。

| 種別 | バックエンド |
|------|------------|
| AMD GPU（ROCm対応） | ROCm |
| NVIDIA GPU | CUDA |
| クラウドGPU（RunPod等） | ROCm / CUDA |

**本論文での検証環境**: AMD R9700 AI Pro（ROCm）・RX9070XT（ROCm）・MI300X（RunPod/ROCm）  
アーキテクチャ上はROCm / CUDA / クラウド全般に対応しています。

---

## Implemented and Validated

MAGIを用いて以下の実験を実施・検証しました。

- 複数GPU環境（ROCm × 3ノード）での並列推論
- 日本語・英語プロンプトで約6,800出力を収集
- `result.json` による自動採点・整合性ハッシュ生成の確認
- MCP経由での実験投入→成果物回収→統合フローの自動化

詳細は論文・スライドを参照してください（[関連資料](#関連資料)）。

---

## Research Findings（検証済み観察）

本フレームワークを用いた実験から、以下の観察が得られました。

- **言語効果は複数GPUで一貫して観測**: EN vs JA の出力差異はハードウェアに依存せず再現する
- **JSON有効率の言語差**: ENプロンプトではJSON生成率が高く、JAプロンプトでは低い傾向
- **モデルサイズ効果**: 大型モデルではJSONフォーマット率が改善するが、`<think>`タグ漏れは残存する
- **主な示唆**: 出力差の主因は推論能力の差ではなく、JSON / think制御における言語依存のボトルネックである可能性

> これらは統計的観察であり、データや詳細な解釈は論文・スライドを参照してください。

---

## MCPツール
MAGIは容易な実験のため、MCPサーバーとしても利用できるよう設計しています。以下にそのツール名と説明を示します。

| ツール名 | 説明 |
|----------|------|
| `submit_experiment` | 実験定義（spec）を投入して `run_id` を発行 |
| `get_status` | ジョブの状態確認 |
| `fetch_artifacts` | `result.json` を取得 |
| `list_runs` | 過去の実験一覧 |
| `get_one_pager` | 発表用1ページ要約を取得 |
| `run_compute` | SSH固定コマンドでCompute Nodeを実行し、成果物回収＋統合 |
| `list_docs_ref` | docs-ref 配下のファイル一覧 |
| `read_docs_ref` | docs-ref 配下のファイルを参照 |

---

## Artifacts / Output Schema

各実験は `<ARTIFACT_DIR>/runs/<run_id>/` に以下を生成します：

- `spec.json` — 実験定義（モデル・データセット・生成パラメータ）
- `env.json` — 実行環境（GPU・ドライバ・OS）
- `responses.jsonl` — モデル出力（1行1アイテム）
- `result.json` — 統合成果物（spec + env + responses + metrics + integrity hash）
- `run.log` — 実行ログ

**`result.json` スキーマ概要**:

```json
{
  "schema_version": "1.0",
  "run_id": "20260212-143052-a1b2c3d4",
  "created_at": "2026-02-12T14:30:52Z",
  "spec": { "..." },
  "spec_hash": "a1b2c3d4",
  "env": { "..." },
  "control_plane": {
    "hostname": "<control-node-hostname>",
    "runner_version": "0.1.0",
    "runner_commit": "abc1234"
  },
  "responses": [ "..." ],
  "metrics": {
    "total": 110,
    "passed": 87,
    "json_parse_rate": 0.79,
    "output_contract_summary": { "..." }
  },
  "integrity": {
    "spec_sha256": "...",
    "responses_sha256": "...",
    "env_sha256": "..."
  }
}
```

> `metrics.json` は独立ファイルとしては生成されません。採点結果は `result.json` の `metrics` フィールドに内包されます。

---

## Setup

### 前提

```
<INSTALL_DIR>/           # このリポジトリのクローン先
├── mcp-server/          # Node.js + TypeScript MCP Server
├── runner/              # Python統合CLI
├── compute-runner/      # Compute Node側Python CLI
├── nodes.yaml           # 接続設定（要編集）
└── db/                  # SQLite (runs.db、自動生成)

<ARTIFACT_DIR>/          # リポジトリ外（configurable）
└── runs/<run_id>/
    ├── spec.json / env.json / responses.jsonl / result.json
```

### 1) MCP Server（Control node側）

```bash
cd <INSTALL_DIR>/mcp-server
npm install
npm run build
node dist/index.js        # stdio起動（推奨）
```

**VS Code からの起動**: `.vscode/mcp.json` があるので、VS Code MCP拡張で stdio 起動できます。

**systemd 常駐（オプション）**:

```bash
# system scope (sudo あり) — deploy/research-mcp.service を先に編集
sudo cp deploy/research-mcp.service /etc/systemd/system/research-mcp.service
sudo systemctl daemon-reload && sudo systemctl enable --now research-mcp.service

# user scope (sudo なし) — deploy/research-mcp.user.service を先に編集
mkdir -p ~/.config/systemd/user
cp deploy/research-mcp.user.service ~/.config/systemd/user/research-mcp.service
systemctl --user daemon-reload && systemctl --user enable --now research-mcp.service
```

動作確認:

```bash
curl http://127.0.0.1:8080/healthz
```

### 2) Runner（Control node側）

```bash
cd <INSTALL_DIR>
python3 -m venv .venv && . .venv/bin/activate
pip install -e runner
```

### 3) Compute Runner（Compute Node側）

```bash
# 各Compute Nodeで実行
pip install -e compute-runner
```

### 4) nodes.yaml の設定

`nodes.yaml` を編集して接続先Compute Nodeを登録してください。コメントアウトされたサンプルを参考にしてください。

---

## Execution Flow

```
1. submit_experiment(spec)    → run_id 発行
2. run_compute(run_id, node)  → SSH経由で推論実行・成果物回収
3. runner integrate <run_id>  → result.json 生成（採点・整合性ハッシュ含む）
4. fetch_artifacts(run_id)    → 結果確認
```

手動での逐次実行も可能です。`runner integrate` は `<ARTIFACT_DIR>/runs/<run_id>/` に
`spec.json` / `env.json` / `responses.jsonl` が揃っていれば単独実行できます。

---

## 動作実証ログ

- 検証内容: `submit_experiment` → `runner integrate` → `result.json` 生成を確認
- MCPツール確認: `get_status=completed`, `list_runs_count=1`, `fetch_has_result=true`
- 生成物: `<ARTIFACT_DIR>/runs/<run_id>/result.json`

---

## 関連資料

- **スライド**: [IEICE総合大会 2026年3月発表](./docs/2026-0312_IEICE.pdf)
- **学会**: IEICE総合大会 2026年3月（九州産業大学）
- **関連論文**: DeepSeek R1 Japanese Language Adaptation (Zenodo)
- **設計メモ・ロードマップ**: [`docs/design-notes.md`](./docs/design-notes.md)
