# STAGE01 Python Batch (eve vs zorya, JP/EN 100 pair)

実行順序はファイル名プレフィックス通りです。

## 1) plan作成
```bash
cd <INSTALL_DIR>/batch_script/STAGE01
python3 1-build_plan.py examples/jp_en_sweep_plan.json
```

## 2) preflight（モデル存在・tasks読込）
```bash
python3 2-preflight_check.py --plan <INSTALL_DIR>/batch_script/STAGE01/plan.stage01.json
```

## 3) 本実行
```bash
python3 3-run_plan.py --plan <INSTALL_DIR>/batch_script/STAGE01/plan.stage01.json
```

主要オプション（段階ゲート向け）:
- `--only-temp t0p1`（`--only` の別名）
- `--only-node zorya`
- `--epochs 1`
- `--limit-units 1`
- `--limit-tasks 20`
- `--timeout-sec-per-task 120`
- `--preflight-timeout-sec 60`
- `--preflight-retries 2`
- `--sleep-between-retries 5`
- `--max-parallel-per-node 1`（現実装は node 内逐次実行のため 0/1 のみ）

出力先:
- `tank/artifacts_py/plans/<plan_id>/plan.json`
- `tank/artifacts_py/plans/<plan_id>/dispatch.table.jsonl`
- `tank/artifacts_py/plans/<plan_id>/dispatch.exec.jsonl`
- `tank/artifacts_py/plans/<plan_id>/run_map.csv`
- `tank/artifacts_py/runs/<run_id>/{spec.json,env.json,tasks.json,responses.jsonl,...}`

### 3.1) smoke実行（高速）
```bash
python3 3-run_plan.py \
  --plan <INSTALL_DIR>/batch_script/STAGE01/plan.stage01.json \
  --only t0p1 --epochs 1 --limit-units 1 --limit-tasks 2
```

### 3.2) raw_output確認（生データ）
```bash
PLAN_ID=$(jq -r .plan_id <INSTALL_DIR>/batch_script/STAGE01/plan.stage01.json)
PLAN_DIR=<TANK_DIR>/artifacts_py/plans/$PLAN_ID
tail -n 3 "$PLAN_DIR/dispatch.exec.jsonl" | jq .
RID=$(tail -n 1 "$PLAN_DIR/dispatch.exec.jsonl" | jq -r '.run_id')
RUN_DIR=<TANK_DIR>/artifacts_py/runs/$RID
head -n 5 "$RUN_DIR/responses.jsonl" | jq -r '.item_id + " -> " + (.raw_output|tostring)'
```

## 4) 集計
```bash
python3 4-summarize_plan.py --plan <INSTALL_DIR>/batch_script/STAGE01/plan.stage01.json
```

## 注意
- `MAGI_OLLAMA_STUB` は使わない（スクリプト内でも `unset`）。
- 既存 `tank/artifacts` とは分離し、`tank/artifacts_py` を使用します。
- 本STAGE01は Python lane 差し戻し運用用です（Rust lane には触れません）。
- `dispatch.exec.jsonl` は `phase=start/end` を記録します（開始・終了判定用）。
- `plan.stage01.json` で `preflight_timeout_sec` / `preflight_retries` / `preflight_prompt` を調整できます（デフォルト: 60秒, 2回）。
