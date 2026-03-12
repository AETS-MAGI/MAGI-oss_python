# STAGE05 Multi Task-Set Orchestration

`pairs_ja_en_*` の全セット（history/calc/freeform/reasoning/phrases/10/100）を順番に回すステージです。

## 1) 全task set用plan生成
```bash
python3 <INSTALL_DIR>/batch_script/STAGE05/1-generate_multi_plans.py
```

## 2) 全plan preflight
```bash
python3 <INSTALL_DIR>/batch_script/STAGE05/2-preflight_all.py
```

## 3) 全plan smoke（推奨: t0p1, epoch=1）
```bash
python3 <INSTALL_DIR>/batch_script/STAGE05/3-run_smoke_all.py --only t0p1 --epochs 1
```

## 4) 全plan full run
```bash
python3 <INSTALL_DIR>/batch_script/STAGE05/4-run_full_all.py
```

## 5) 全plan integrate + aggregate
```bash
python3 <INSTALL_DIR>/batch_script/STAGE05/5-integrate_and_summarize_all.py
```

出力:
- plans manifest: `STAGE05/plans/manifest.json`
- artifacts: `tank/artifacts_py/plans/<plan_id>`, `tank/artifacts_py/runs/<run_id>`

