# 改善案（Python lane / STAGE01向け）

背景

現状の失敗は、ノード全体の停止ではなく **「特定の1リクエストだけ異常に長引く」**タイプで発生している。
例：P003:ja が timeout_sec_per_task=120s を超過し kill → compute.exit.json で failed_with_evidence。直後の P003:en は成功しており、サービス全停止ではない。

このタイプは **Ollama側の一時的な詰まり（モデルロード／GPU待ち／生成停滞／RPC詰まり）**で起きやすく、単純な「失敗＝環境破綻」とは切り分ける必要がある。

目的
	•	実験を止めずに「一時的な詰まり」を吸収し、30epochスイープを完走させる
	•	失敗時の証拠（compute.exit.json / stderr log / partial responses）を必ず残し、後から解析可能にする
	•	りもこが「結果（生出力）を読める」状態を維持する

⸻

改善方針

1) 失敗も必ず「証拠付きで残す」を絶対化

各 run について、成功/失敗どちらでも以下を残す。
	•	responses.jsonl：成功した分は必ず残す（部分成功でもOK）
	•	compute.stderr.ok.log：成功時の stderr を集約
	•	compute.stderr.log：失敗時の stderr を集約（どの item で何が起きたか）
	•	compute.exit.json：失敗時の最終判定（magi-compute-exit-v0）

※「dispatch.exec.jsonl に started/end が両方入っている」ことも必須。endが無いrunはハング検知対象とする。

⸻

2) Timeout を「即死」ではなく「1回だけ救う」(最小リトライ)

Timeout は一時的要因が多いので、同一 item だけ 1回再試行する。

推奨ルール：
	•	TimeoutExpired のとき
	•	sleep 3〜5秒
	•	同じ item_id を 1回だけ ollama run で再実行
	•	2回目も Timeout または非0終了なら、その item は failure として記録し、run全体は failed_with_evidence に落とす
	•	それまでに成功した tasks の responses.jsonl は保持（部分成功の証拠）

この変更で、今回のような「P003:ja だけ偶発的に詰まった」ケースを吸収できる可能性が高い。

⸻

3) 事前ゲート（段階拡大）を固定運用にする

いきなり30epochへ行かず、以下の段階を必須にする（既に案があるが、運用ルールとして固定する）。
	•	S0 Preflight：2問・epoch1・timeout60s・retry2
→ 通らない組（node×temp）は本番投入しない
	•	S1 Short run：20問・epoch1・timeout120s
→ responsesが20行揃うこと（欠損があれば原因切り分け）
	•	S2 Full tasks / single epoch：200問・epoch1・timeout180s
→ analyzerがreport.mdを出せることを確認
	•	S3 Full run：200問×30epoch
→ 温度は t0p1→t0p2→t0p0→t0p7 の順で段階投入

⸻

4) 「Ollamaが詰まってるだけ」を判定できるログを残す

失敗時の compute.exit.json の issues に以下を入れる（既に近い形はあるので、追加方針）。
	•	item_id
	•	failure_type（timeout / nonzero_exit / stderr_only など）
	•	elapsed_ms（timeoutの時は確定で）
	•	attempt（1回目/2回目）
	•	model_tag
	•	node_id

また compute.stderr.(ok.)log には最低限
	•	cmd
	•	exit_code
	•	stub_mode（常に off / または none）
を含める。

⸻

期待される効果
	•	「1問だけ詰まって全体が死ぬ」を減らせる
	•	失敗しても証拠が揃うので、解析・論文化・再現が可能
	•	zorya/eve の比較を “同じ手順” で回せる

⸻

次アクション（最小）
	•	Python runner（remote_runner_script 内）に Timeout時の1回再試行を追加
	•	失敗時でも必ず responses.jsonl を保持することを確認（今の実装方針はOK）
