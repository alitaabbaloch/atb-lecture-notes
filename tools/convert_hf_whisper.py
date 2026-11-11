#!/usr/bin/env python3
"""Convert a Whisper fine-tune from Hugging Face (transformers format, safetensors) to the folder
layout mlx-whisper loads: config.json + weights.safetensors.

Usage: .venv/bin/python tools/convert_hf_whisper.py <hf-repo-or-folder> <output-folder>

Only `config.json` and `model.safetensors` are read. No torch and no transformers are needed.
The output folder can then be used as the model: WHISPER_MODEL=<output-folder>.
"""
import json
import sys
from pathlib import Path

import mlx.core as mx
from mlx.utils import tree_flatten

RENAMES = (
    (".layers.", ".blocks."),
    (".self_attn_layer_norm.", ".attn_ln."),
    (".encoder_attn_layer_norm.", ".cross_attn_ln."),
    (".final_layer_norm.", ".mlp_ln."),
    (".self_attn.", ".attn."),
    (".encoder_attn.", ".cross_attn."),
    (".q_proj.", ".query."),
    (".k_proj.", ".key."),
    (".v_proj.", ".value."),
    (".out_proj.", ".out."),
    (".fc1.", ".mlp1."),
    (".fc2.", ".mlp2."),
)


def source_files(source: str) -> tuple[Path, Path]:
    folder = Path(source).expanduser()
    if folder.is_dir():
        return folder / "config.json", folder / "model.safetensors"
    from huggingface_hub import hf_hub_download
    return Path(hf_hub_download(source, "config.json")), Path(hf_hub_download(source, "model.safetensors"))


def dimensions(hf: dict) -> dict:
    return {
        "n_mels": hf["num_mel_bins"],
        "n_audio_ctx": hf["max_source_positions"],
        "n_audio_state": hf["d_model"],
        "n_audio_head": hf["encoder_attention_heads"],
        "n_audio_layer": hf["encoder_layers"],
        "n_vocab": hf["vocab_size"],
        "n_text_ctx": hf["max_target_positions"],
        "n_text_state": hf["d_model"],
        "n_text_head": hf["decoder_attention_heads"],
        "n_text_layer": hf["decoder_layers"],
    }


def rename(key: str) -> str | None:
    """transformers name -> mlx-whisper name, or None for arrays mlx-whisper does not load."""
    if key.startswith("model."):
        key = key[len("model."):]
    if key == "proj_out.weight" or key == "encoder.embed_positions.weight":
        return None  # tied to the token embedding / recomputed as sinusoids
    if key == "decoder.embed_tokens.weight":
        return "decoder.token_embedding.weight"
    if key == "decoder.embed_positions.weight":
        return "decoder.positional_embedding"
    if key.startswith("encoder.layer_norm."):
        return key.replace("encoder.layer_norm.", "encoder.ln_post.")
    if key.startswith("decoder.layer_norm."):
        return key.replace("decoder.layer_norm.", "decoder.ln.")
    for old, new in RENAMES:
        key = key.replace(old, new)
    return key


def convert(source: str, out: str) -> dict:
    config_file, weights_file = source_files(source)
    dims = dimensions(json.loads(config_file.read_text(encoding="utf-8")))
    raw = mx.load(str(weights_file))
    weights = {}
    for key, value in raw.items():
        name = rename(key)
        if name is None:
            continue
        if name.endswith(("conv1.weight", "conv2.weight")):
            value = value.swapaxes(1, 2)  # (out, in, kernel) -> (out, kernel, in)
        weights[name] = value.astype(mx.float16)

    # Every parameter the model has must be present with the right shape, and nothing extra.
    from mlx_whisper import whisper
    model = whisper.Whisper(whisper.ModelDimensions(**dims), mx.float16)
    wanted = {k: tuple(v.shape) for k, v in tree_flatten(model.parameters()) if k != "alignment_heads"}
    got = {k: tuple(v.shape) for k, v in weights.items()}
    missing = sorted(set(wanted) - set(got))
    extra = sorted(set(got) - set(wanted))
    wrong = sorted(k for k in set(wanted) & set(got) if wanted[k] != got[k])
    if missing or extra or wrong:
        raise SystemExit(f"conversion does not match the model:\n missing {missing[:6]}\n extra {extra[:6]}\n"
                         f" wrong shape {[(k, got[k], wanted[k]) for k in wrong[:6]]}")

    out_dir = Path(out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    mx.save_safetensors(str(out_dir / "weights.safetensors"), weights)
    (out_dir / "config.json").write_text(json.dumps({**dims, "model_type": "whisper"}, indent=4), encoding="utf-8")
    return {"arrays": len(weights), "out": str(out_dir), **dims}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    info = convert(sys.argv[1], sys.argv[2])
    print(json.dumps(info, indent=1))
