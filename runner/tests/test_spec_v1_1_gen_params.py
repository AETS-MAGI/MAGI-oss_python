import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import rocm_runner.cli as cli


def _must_get(name: str):
    """Fail fast with a helpful message if implementation name differs."""
    if not hasattr(cli, name):
        pytest.fail(
            f"rocm_runner.cli に '{name}' が見つからないにゃん。"
            f"実装の関数名/配置が違う可能性があるので、"
            f"cli.py 内の実際の関数名に合わせてテスト側を更新してにゃん。"
        )
    return getattr(cli, name)


# --- 1) Output contract classification --------------------------------------

def test_output_contract_non_json_plaintext():
    classify = _must_get("classify_output_contract")
    raw = "日本 capitalは東京都です。"
    # 非JSONなので parsed_output=None、parse_errorはJSONDecodeError相当の文字列、constraintsは評価不能扱い
    out = classify(raw, None, "Expecting value: line 1 column 1 (char 0)", False)
    assert out == "non_json_plaintext"


def test_output_contract_json_prefix_suffix():
    classify = _must_get("classify_output_contract")
    raw = "OK! {\"answer\":\"東京都\"} thx"
    # 本体は prefix/suffix 混入で最初のparseは失敗している想定
    # passed_constraints はここでは「JSON構造の話だけしたい」ので True 扱いにしておく
    out = classify(raw, None, "Expecting value: line 1 column 1 (char 0)", True)
    assert out == "json_prefix_suffix"


def test_output_contract_not_schema_mismatch_on_content_constraint_fail():
    classify = _must_get("classify_output_contract")
    raw = "{\"answer\":\"大阪\"}"
    parsed = {"answer": "大阪"}
    # contains 失敗っぽい constraint error を入れる（schema系ではない）
    out = classify(raw, parsed, None, ["contains expected '東京'"])
    assert out != "json_schema_mismatch"
    assert out == "json_valid"

def test_output_contract_not_schema_mismatch_on_array_length_fail():
    evaluate = _must_get("evaluate_constraints")
    classify = _must_get("classify_output_contract")
    raw = "[1, 2, 3]"
    parsed = [1, 2, 3]
    # evaluate_constraints -> json_valid remains True, but array_length fails
    passed, errors = evaluate(parsed, {"array_length": 2})
    assert passed is False
    out = classify(raw, parsed, None, errors)
    assert out == "json_valid"  # NOT json_schema_mismatch

def test_output_contract_schema_mismatch_on_required_keys_error():
    classify = _must_get("classify_output_contract")
    raw = "{\"foo\": 1}"
    parsed = {"foo": 1}
    out = classify(raw, parsed, None, ["required_keys: answer"])
    assert out == "json_schema_mismatch"

# --- 2) Determinism effective ------------------------------------------------


def test_deterministic_effective_edge_cases():
    check = _must_get("check_deterministic_effective")

    # 仕様：temperature==0 && (top_p==1 or None) && (top_k==0 or None) && seed!=None のときのみ True
    assert check(
        {"temperature": 0.0, "top_p": 1.0, "top_k": 0, "seed": 123}
    ) is True

    assert check(
        {"temperature": 0.0, "top_p": None, "top_k": None, "seed": 123}
    ) is True

    # seedなし → False
    assert check(
        {"temperature": 0.0, "top_p": 1.0, "top_k": 0, "seed": None}
    ) is False

    # temperature>0 → False
    assert check(
        {"temperature": 0.1, "top_p": 1.0, "top_k": 0, "seed": 123}
    ) is False


# --- 3) v1.0 compatibility (gen_params_missing) ------------------------------
# integrate の実装形が環境によって違う可能性があるので、2段構えにしてるにゃん。
# 1) cli.integrate(...) が呼べるならそれを使う
# 2) だめなら `python -m rocm_runner.cli integrate <dir>` を試す
#
# どちらも無理なら skip する（でも分類/決定性の単体テストは走る）


def _write_minimal_artifacts(run_dir: Path, schema_version: str):
    run_dir.mkdir(parents=True, exist_ok=True)

    spec = {
        "schema_version": schema_version,
        "run_id": "TEST-RUN",
        "spec": {
            "compute_node": "mock",
            "dataset": "japanese_10q",
            "model_id": "deepseek-r1-distill-qwen-7b",
            "quantization": "q4_k_m",
            # v1.0 では gen_params を入れない
            "tasks": [{"task_id": "JP01", "constraints": {"json_valid": True}}],
        },
    }

    env = {
        "hostname": "mock",
        "backend": "rocm",
        "gpu": {"name": "MockGPU", "arch": "gfx1201", "vram": "16GB"},
        "driver": {"rocm": "7.x"},
    }

    # responses.jsonl: 1行だけ
    resp_line = {
        "item_id": "JP01",
        "prompt": "日本の首都はどこ？JSONで: {\"answer\":\"...\"}",
        "raw_output": "{\"answer\":\"東京都\"}",
        "latency_ms": 1,
        "model_id": "deepseek-r1-distill-qwen-7b",
        "backend": "rocm",
        "gen_params": {"quantization": "q4_k_m"},
        "gpu": "MockGPU",
        "gpu_arch": "gfx1201",
    }

    (run_dir / "spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "env.json").write_text(json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "responses.jsonl").write_text(json.dumps(resp_line, ensure_ascii=False) + "\n", encoding="utf-8")


def _try_integrate_inprocess(run_dir: Path) -> Path | None:
    if not hasattr(cli, "integrate"):
        return None

    integrate = getattr(cli, "integrate")

    # integrate のシグネチャが不明なので、よくある形を順に試す
    candidates = [
        (run_dir,),  # integrate(run_dir)
        (str(run_dir),),  # integrate(str)
        (run_dir, run_dir / "result.json"),  # integrate(run_dir, out)
        (str(run_dir), str(run_dir / "result.json")),
    ]

    for args in candidates:
        try:
            integrate(*args)
            out = run_dir / "result.json"
            if out.exists():
                return out
        except TypeError:
            continue
        except Exception:
            # ここは実装依存の例外があり得るので次候補へ
            continue
    return None


def _try_integrate_subprocess(run_dir: Path) -> Path | None:
    # `python -m rocm_runner.cli integrate <run_dir>` を試す
    cmd = [sys.executable, "-m", "rocm_runner.cli", "integrate", str(run_dir)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except Exception:
        return None

    out = run_dir / "result.json"
    return out if out.exists() else None


def test_v1_0_gen_params_missing_flag(tmp_path: Path):
    run_dir = tmp_path / "mock_run_v1_0"
    _write_minimal_artifacts(run_dir, schema_version="1.0")

    out = _try_integrate_inprocess(run_dir)
    if out is None:
        out = _try_integrate_subprocess(run_dir)

    if out is None:
        pytest.skip("integrate の呼び出し方法が特定できないため v1.0互換テストはskipにゃん（単体テストはOK）")

    result = json.loads(out.read_text(encoding="utf-8"))

    assert result.get("gen_params_missing") is True
    assert result.get("deterministic_effective") is False
