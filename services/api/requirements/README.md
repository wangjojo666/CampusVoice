# Python dependency locks

CampusVoice uses Python 3.11 and keeps two hash-checked lock files:

- `runtime.lock` contains the API's default runtime dependencies.
- `dev.lock` contains the runtime and `dev` extra used by tests, linting, type checking, migrations, and dependency auditing.

Install a lock from `services/api` with pip's hash enforcement enabled:

```bash
python -m pip install --require-hashes --requirement requirements/runtime.lock
python -m pip install --require-hashes --requirement requirements/dev.lock
```

## Regenerating and checking locks

Use Python 3.11 and pip-tools 7.5.3 so generated output stays reviewable:

```bash
python -m pip install "pip-tools==7.5.3"
python -m piptools compile \
  --generate-hashes \
  --index-url=https://pypi.org/simple \
  --output-file=requirements/runtime.lock \
  --strip-extras \
  pyproject.toml
python -m piptools compile \
  --allow-unsafe \
  --extra=dev \
  --generate-hashes \
  --index-url=https://pypi.org/simple \
  --output-file=requirements/dev.lock \
  --strip-extras \
  pyproject.toml
```

After changing `pyproject.toml`, regenerate both files and review the complete dependency diff. CI's Linux installation is the portability check. A local freshness check is:

```bash
git diff --exit-code -- requirements/runtime.lock requirements/dev.lock
python -m pip_audit --require-hashes --disable-pip -r requirements/runtime.lock
python -m pip_audit --require-hashes --disable-pip -r requirements/dev.lock
```

Do not hand-edit a lock or install it without `--require-hashes`.

## AI, CPU, and CUDA installations

The `ai` extra is intentionally absent from the generic locks. PyTorch wheels are platform- and accelerator-specific, while CUDA wheels use a separate official index and local version suffix such as `+cu130`. A single cross-platform hash lock would either select the wrong build or falsely imply that every supported machine uses the same artifacts.

Install the core lock first, then install an exact, matching `torch` and `torchaudio` pair from the official PyTorch index, and finally install the AI extra. For the current CUDA 13.0 image strategy:

```bash
python -m pip install --require-hashes -r requirements/runtime.lock
python -m pip install \
  "torch==2.11.0+cu130" \
  "torchaudio==2.11.0+cu130" \
  --index-url https://download.pytorch.org/whl/cu130
python -m pip install ".[ai]"
```

For CPU-only systems, choose the matching `+cpu` pair and official CPU wheel index instead. Keep `torch` and `torchaudio` on the same public version and build variant. The API Dockerfile exposes `INSTALL_AI`, `PYTORCH_VERSION`, `PYTORCH_VARIANT`, and `PYTORCH_INDEX_URL` so CI/default images remain AI-disabled while GPU deployments make their wheel choice explicit.

Treat the AI installation as a separately reviewed, hardware-specific layer: use only official indexes, inspect the resolver output, run an import/device check and a model smoke test on the target hardware, and record the exact model artifacts deployed. Hash locks do not cover OS packages, CUDA drivers, or downloaded model weights.
