# MAGI Design Notes & Implementation Roadmap

> このドキュメントは開発メモ・ロードマップを記録する内部向け資料です。
> 公開READMEには含めない情報を管理します。

---

## 接続運用ポリシー（参考）

- Claude Desktop との HTTP/SSE 接続は不安定なため、VS Code Remote-SSH / MCP クライアントから
  Control node に入って stdio 起動（`node dist/index.js`）を第一選択とする。
- HTTP/SSE は必要時のみ。systemd ユニットや `/healthz`, `/sse` エンドポイントは実装済み。

---

## Metadata Scope

### Phase 1（実装済み・本発表対象）

記録するメタデータは再現可能な評価に最低限必要なものに限定している。

- GPU name / VRAM capacity
- Driver version (ROCm / CUDA)
- Inference backend
- hostname, OS

### Phase 2 以降（未実装・予定）

以下は性能ベンチマーク向けのフィールドであり、Phase 1 の研究主張（再現可能性の確認）とは独立している。
`schema_version` インクリメントにより後方互換で追加予定。

- `tensor_parallel_size`
- `peak_vram_mb`
- `ttft_ms` (Time to First Token)
- `tokens_per_sec`
- detailed performance telemetry

---

## 設計メモ

- `RUNNER_CONTRACT.md`: Control Plane Runner の入出力契約
- `COMPUTE_RUNNER_CONTRACT.md`: Compute Node 側 `compute-runner` の入出力契約
- `nodes.yaml`: SSH経由でCompute Nodeを扱うための接続定義
- `deploy/research-mcp.service`: MCP Server 常駐化用 systemd ユニット

---

## 証拠 run 方針

以下4本の証拠 run を `<ARTIFACT_DIR>/runs/` に残す（手動実行で OK）。

1. 正常系 run（spec + env + responses → result.json）
2. 旧形式互換 run（`task_id/output` 形式）
3. 壊れ行混入 run（`metrics.errors[]` に記録しつつ継続）
4. 必須欠落 run（`spec.json`/`env.json`/`responses.jsonl` 欠落で exit 1）

---

## MCP Server ディレクトリ構造（参考）

```
<INSTALL_DIR>/                          # Repository root
├── mcp-server/     # Node.js + TypeScript MCP Server
├── runner/         # Python CLI (`runner integrate <run_id>`)
├── compute-runner/ # Python CLI deployed on each compute node
├── nodes.yaml      # Compute node connection config
└── db/             # SQLite: runs.db

<ARTIFACT_DIR>/                         # Outside repo (configurable)
├── runs/<run_id>/
│   ├── spec.json
│   ├── responses.jsonl
│   ├── env.json
│   └── result.json
└── docs-ref/       # Optional: reference documents
```
