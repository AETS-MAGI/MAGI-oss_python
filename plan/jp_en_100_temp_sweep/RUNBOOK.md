# JP/EN 100 Pair Temp Sweep Runbook (Rust MAGI / Ollama)

## Scope

- Dataset: `pairs_ja_en_100.json` -> expanded to 200 tasks (`item_id = PNNN:ja|en`)
- Nodes: `zorya`, `eve`
- Backend: `ollama` (`accel=rocm`)
- Sweep temperatures: `0.0 / 0.1 / 0.2 / 0.7`
- Epochs: `30`, Replicates: `1`
- Non-stub rule: `MAGI_OLLAMA_STUB` must be unset

## Generated Files

- Tasks:
  - `<RUST_REPO_DIR>/tasks/generated/jp_en_100_tasks.json`
- Plans:
  - `jp_en100_zorya_t0p0_e30.json`
  - `jp_en100_zorya_t0p1_e30.json`
  - `jp_en100_zorya_t0p2_e30.json`
  - `jp_en100_zorya_t0p7_e30.json`
  - `jp_en100_eve_t0p0_e30.json`
  - `jp_en100_eve_t0p1_e30.json`
  - `jp_en100_eve_t0p2_e30.json`
  - `jp_en100_eve_t0p7_e30.json`

## Model Tag Alignment (both nodes verified)

- `deepseek-r1-distill-qwen-7b-q4_k_m-t0p0:latest`
- `deepseek-r1-distill-qwen-7b-q4_k_m-t0p1:latest`
- `deepseek-r1-distill-qwen-7b-q4_k_m-t0p2:latest`
- `deepseek-r1-distill-qwen-7b-q4_k_m-t0p7:latest`

## Preflight (per node, t0p1 first)

### zorya

```bash
ssh YOUR_USER@YOUR_HOST_ZORYA '
set -euo pipefail
BIN=<MAGI_BIN_DIR>
PLAN=<MAGI_PLANS_DIR>/jp_en100_zorya_t0p1_e30.json
export MAGI_RUNNER_BIN=$BIN/magi-runner
unset MAGI_OLLAMA_STUB

$BIN/magi-master plan validate "$PLAN" --json
$BIN/magi-master plan submit "$PLAN"
$BIN/magi-master dispatch local --plan jp_en100_zorya_t0p1_e30_20260306 --limit 1

PLAN_DIR=<TANK_DIR>/artifacts/plans/jp_en100_zorya_t0p1_e30_20260306
RID=$(tail -n 1 "$PLAN_DIR/dispatch.exec.jsonl" | jq -r .run_id)
RUN_DIR=<TANK_DIR>/artifacts/runs/$RID

$BIN/magi-master integrate --run "$RID"
echo "RID=$RID"
wc -l "$RUN_DIR/responses.jsonl"
head -n 2 "$RUN_DIR/responses.jsonl" | jq -r .raw_output | sed -n "1,2p"
'
```

### eve

```bash
ssh YOUR_USER@YOUR_HOST_EVE '
set -euo pipefail
BIN=<MAGI_BIN_DIR>
PLAN=<MAGI_PLANS_DIR>/jp_en100_eve_t0p1_e30.json
export MAGI_RUNNER_BIN=$BIN/magi-runner
unset MAGI_OLLAMA_STUB

$BIN/magi-master plan validate "$PLAN" --json
$BIN/magi-master plan submit "$PLAN"
$BIN/magi-master dispatch local --plan jp_en100_eve_t0p1_e30_20260306 --limit 1

PLAN_DIR=<TANK_DIR>/artifacts/plans/jp_en100_eve_t0p1_e30_20260306
RID=$(tail -n 1 "$PLAN_DIR/dispatch.exec.jsonl" | jq -r .run_id)
RUN_DIR=<TANK_DIR>/artifacts/runs/$RID

$BIN/magi-master integrate --run "$RID"
echo "RID=$RID"
wc -l "$RUN_DIR/responses.jsonl"
head -n 2 "$RUN_DIR/responses.jsonl" | jq -r .raw_output | sed -n "1,2p"
'
```

## Full Sweep (after preflight non-stub passed)

### zorya plans

```bash
for P in <MAGI_PLANS_DIR>/jp_en100_zorya_t0p{0,1,2,7}_e30.json; do
  BIN=<MAGI_BIN_DIR>
  PLAN_ID=$(jq -r .plan_id "$P")
  export MAGI_RUNNER_BIN=$BIN/magi-runner
  unset MAGI_OLLAMA_STUB
  $BIN/magi-master plan validate "$P" --json
  $BIN/magi-master plan submit "$P"
  $BIN/magi-master dispatch local --plan "$PLAN_ID" --limit 30
  PLAN_DIR=<TANK_DIR>/artifacts/plans/$PLAN_ID
  jq -r .run_id "$PLAN_DIR/dispatch.exec.jsonl" | while read -r RID; do
    $BIN/magi-master integrate --run "$RID"
  done
  $BIN/magi-master plan aggregate --plan "$PLAN_ID" > "/tmp/${PLAN_ID}.aggregate.txt"
done
```

### eve plans

```bash
for P in <MAGI_PLANS_DIR>/jp_en100_eve_t0p{0,1,2,7}_e30.json; do
  BIN=<MAGI_BIN_DIR>
  PLAN_ID=$(jq -r .plan_id "$P")
  export MAGI_RUNNER_BIN=$BIN/magi-runner
  unset MAGI_OLLAMA_STUB
  $BIN/magi-master plan validate "$P" --json
  $BIN/magi-master plan submit "$P"
  $BIN/magi-master dispatch local --plan "$PLAN_ID" --limit 30
  PLAN_DIR=<TANK_DIR>/artifacts/plans/$PLAN_ID
  jq -r .run_id "$PLAN_DIR/dispatch.exec.jsonl" | while read -r RID; do
    $BIN/magi-master integrate --run "$RID"
  done
  $BIN/magi-master plan aggregate --plan "$PLAN_ID" > "/tmp/${PLAN_ID}.aggregate.txt"
done
```

## Analyzer

```bash
python3 <RUST_REPO_DIR>/analysis/analyze_pairs.py \
  --tank-root <TANK_DIR> \
  --artifact-root artifacts \
  --gold <RUST_REPO_DIR>/analysis/gold_answers.json \
  --out-dir <TANK_DIR>/tmp_bak/magi_out_pairs_now
```

## Hard Stop Conditions

- `responses.jsonl` first lines start with `stubbed-output:` -> STOP.
- `responses.jsonl` line count != 200 in preflight -> STOP.
- `spec.tasks` missing and `tasks.json` missing -> STOP.
