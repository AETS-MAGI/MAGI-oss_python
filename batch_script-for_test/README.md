# Python Batch Stages (Rollback Lane)

- `STAGE01`: plan作成 / preflight / 実行 / run内サマリ
- `STAGE02`: 進捗監視 / 失敗一覧 / 再実行ガイド
- `STAGE03`: run結果統合（result.json）/ plan集計
- `STAGE04`: 解析実行 / ノートリンク更新
- `STAGE05`: 全task set一括オーケストレーション（生成/事前確認/smoke/full）

推奨実行順:
1. `STAGE01`
2. `STAGE02`
3. `STAGE03`
4. `STAGE04`
5. `STAGE05`（必要時）
