# Host 10.9.66.193: GPU works for inference, cannot fine-tune Laya

**Date:** 2026-10-14
**Subject:** can this host run the Laya fine-tuning smoke test?
**Status:** **no.** Inference yes, autograd no. Evidence below.
**Nothing installed, nothing modified on the host.**

---

## Bottom line

The RX 6600 XT on 10.9.66.193 runs models successfully through **Vulkan + Ollama**,
but **no autograd-capable backend can compute on it**. Fine-tuning needs autograd,
so the training run cannot happen here. This host is a working inference box and
should be treated as one.

## What is proven working

```
$ live inference on llama3-groq-tool-use:latest (4.66 GB)
  response   : "GPU OK"
  eval_count : 3
  eval_rate  : 39.8 tok/s
  load      : 11.2 s
```

Also present and healthy: Vulkan 1.3.280/1.4.311 with **RADV NAVI23** enumerated
as `DISCRETE_GPU` (Mesa 25.1.5), `amdgpu` loaded, `/dev/dri/renderD128` present,
user in `video` + `render`. 9 models loaded, 31 GB RAM, 183 GB free.

## What is dead, with the specific error

**ROCm/HIP — dead:**

```
RuntimeError: HIP error: invalid device function
```

`lspci` confirms `Navi 23 [Radeon RX 6600/6600 XT] [1002:73ff]`. ROCm 6.2 does not
officially support gfx1032 (Navi 23), so `torch 2.5.1+rocm6.2` enumerates the
device (`device_count: 1`, 7 GiB) but **every kernel launch fails**. Enumerating
is not computing — `torch.cuda.is_available()` returning `True` is misleading
here.

**Vulkan autograd — absent:**

```
torch.utils.vulkan    : ModuleNotFoundError
torch has use_vulkan  : False
torch_directml        : not installed
ggml_vk               : not exposed
vkcube                : rc=1 (headless, so inconclusive but not encouraging)
```

`llama_cpp 0.3.31` is installed but exposes no Python-visible ggml-vulkan backend,
and ggml has no autograd regardless — it is an inference engine.

## Why 8 GB also would not have been enough

Even if a backend worked, VRAM is the binding constraint for a 421M-parameter
model:

| Component | Size |
|---|---|
| weights fp32 | 1.57 GiB |
| gradients fp32 | 1.57 GiB |
| Adam m + v fp32 | 3.14 GiB |
| **minimum total** | **6.27 GiB** |
| + activations (batch-dependent) | |

Against ~7.0 GiB usable on an 8 GB card, that leaves almost no room for
activations. Typical workarounds are each a compromise: frozen encoder
(`freeze_encoder=True`), gradient checkpointing, or reduced batch/accumulation.
The A4000's 16 GB was the right target.

## What I did NOT do

Did not install ROCm, did not attempt the unofficial `HSA_OVERRIDE_GFX_VERSION`
route, did not modify the host's CUDA/Vulkan configuration, did not install a
custom torch build. The `HSA_OVERRIDE_GFX_VERSION` env var is unset. Any of these
could plausibly make the ROCm path *partially* work on Navi 23, but each is a
system-level change to a host that currently serves 9 models successfully, and I
was not authorized to experiment on it.

## Recommendation

Keep 10.9.66.193 as the inference host. For training, the requirement is a
**CUDA-capable NVIDIA GPU** (the A4000, or the documented Kaggle two-T4
workflow) — not more effort on Vulkan here.

If you want me to try the Navi 23 ROCm override *anyway*, say so explicitly and I
will attempt it in a way that leaves the host's working setup intact — but my
expectation is that it fails at kernel launch, because that is the exact failure
already observed.

## Boundaries

Read-only with respect to the host except `/tmp` probes. No system packages
installed, no environment variables set, no model files touched, Ollama left
untouched and running.
