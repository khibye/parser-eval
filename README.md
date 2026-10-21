# parser-eval

A web tool for comparing document parsers end to end, the way Open WebUI uses them. Every file in an
S3 folder goes to two or more docintel instances (for example docintel with Xberg 1.0.14 and with
Xberg 1.3.10), and the tool shows what got better, what got worse, and why.

- **Same request as Open WebUI.** Each parser is called like Open WebUI calls Docling: a multipart
  `POST /v1/convert/file` with the Docling parameters as form fields, and the text read from
  `document.md_content`.
- **Checks per file.** Small pass/fail assertions about the output (see below). They are written once
  and run on every version.
- **Judge model.** Answers question checks, picks the better of two outputs when the text changed a lot
  (asked in both orders to cancel position bias), suggests checks, and scores VLM descriptions.
- **VLM isolation.** docintel's VLM calls go through a caching proxy, so two parser versions get the
  same VLM answer for the same image and the comparison shows only what the parser changed.
- **Persistent.** Settings, parsers, checks, run outputs and reports are files on a PVC. A run that
  stops when the pod restarts can be resumed.

## Layout

```
app/        FastAPI backend (UI and API on 8080, VLM proxy on 8081)
static/     Browser UI, plain HTML/CSS/JS, no build step and nothing loaded from the internet
samples/    Synthetic test set with ready-made checks (imported to S3 from the UI)
chart/      Helm chart for OpenShift (Route, PVC, SealedSecret, optional NetworkPolicy)
scripts/    seal-secrets.sh for the SealedSecret values
tools/      gen_samples.py (rebuilds samples), mock_services.py (fake docintel and LiteLLM)
tests/      Unit tests for the check engine
```

## Install on OpenShift

### 1. Build the image

The base image is `python:3.11-slim`. Packages are pinned to early-2024 releases (see
`requirements.txt`) so they exist on older mirrors.

```bash
podman build -t parser-eval:1.0.0 \
  --build-arg BASE_IMAGE=<your-registry>/python:3.11-slim \
  --build-arg PIP_INDEX_URL=https://<your-mirror>/simple \
  --build-arg PIP_TRUSTED_HOST=<your-mirror-host> .
podman push parser-eval:1.0.0 <your-registry>/eval/parser-eval:1.0.0
```

The image runs as any UID in group 0 and with a read-only root filesystem.

### 2. Seal the two secrets

```bash
./scripts/seal-secrets.sh <namespace> parser-eval-secrets <controller-namespace> <controller-name>
```

It asks for the first sign-in password, generates `APP_SECRET_KEY`, and prints a values snippet with
both values sealed for that namespace and secret name. Save it as `values-prod.yaml`.

`APP_SECRET_KEY` encrypts the keys you save in Settings and signs sessions. Do not change it after the
first start, or saved keys can no longer be read (you would type them again in Settings).

### 3. Install

```bash
helm upgrade --install parser-eval chart/parser-eval -n <namespace> \
  -f values-prod.yaml \
  --set image.repository=<your-registry>/eval/parser-eval --set image.tag=1.0.0
```

Useful values: `persistence.size`, `persistence.storageClass`, `route.host`, `networkPolicy.enabled`,
`resources`. The PVC and the SealedSecret are kept on `helm uninstall`.

### 4. First setup in the browser

1. **Settings > File storage:** endpoint, bucket, keys, path-style addressing for most internal S3
   services, and the internal CA if TLS uses one. Press *Test connection*.
2. **Settings > Judge:** LiteLLM URL (for example `http://litellm.<ns>.svc:4000/v1`), model, key.
   Tick *This model can see images* if it does. Press *Test judge*.
3. **Settings > VLM cache:** the LiteLLM URL that VLM calls are forwarded to.
4. **Parsers:** add one entry per docintel instance. Paste the same JSON you use for `DOCLING_PARAMS`
   in Open WebUI.
5. In every docintel instance you evaluate, set the VLM URL to the address shown in Settings > VLM
   cache (`http://parser-eval.<ns>.svc:8081/v1`). Keep its LiteLLM key; the proxy passes it through.
6. **Settings > Access:** change the password.
7. **Files & tests > Add sample set**, then **New run**.

## Checks

A check is one thing that must be true about a file's output. Writing a few per file turns "I look at
the output and it seems fine" into something the tool verifies on every version.

| Type | Passes when |
|---|---|
| Contains | The text appears. Close match (90% similar by default) or exact. |
| Not contains | The text does not appear, for example a leftover `<!-- image -->`. |
| Order | The parts appear in this order. Catches broken reading order. |
| Appears once | The text appears exactly once. Catches duplicated OCR text. |
| Image blocks | The output has exactly (or at least) N `<image_analysis>` blocks. |
| Image contains | Image block N contains the text. Catches images attached to the wrong place. |
| Table cell | One table row (or line) holds both the label and the value. |
| Question | The judge answers the question from the output only, and the answer must match. |

Before comparing, the text is normalized: niqqud, direction marks, quote styles and extra spaces are
removed. A practical routine for a real file: open it in *Files & tests*, parse it once, press
*Suggest checks*, then approve only the drafts that are true in the original file, and add one or two
of your own for what matters most in that file. Selecting text in the output also starts a check.

Checks are tied to the file version in S3. When the file changes, it is flagged until you review its
checks and mark them as current. Each run freezes the checks as they were when it started.

## What a comparison shows

- **Regressed:** B passes fewer of the file's checks than A, or B failed where A did not.
- **Improved:** the opposite.
- **Needs review:** the checks did not change, but the text changed by more than the threshold (15% by
  default). These are good candidates for new checks.
- **Judge picks:** for files over the threshold, which output is more faithful and complete.
- Time per file (median and p95), errors and timeouts, by file type.

*Download report* saves a self-contained HTML report of the comparison.

## VLM quality

A separate flow for the VLM itself: pick an image folder in S3 (its subfolders become image kinds), and
two or more setups (model, prompt, resize). Each setup describes every image through LiteLLM, and the
judge scores each description from 1 to 5 on accuracy, completeness, text reading, invented details and
overall, without knowing which setup wrote it.

## Data on the PVC

```
/data/config/    settings.json (keys encrypted), parsers.json, auth.json, presets.json
/data/checks/    one JSON per S3 file
/data/runs/      run.json, results/<parser>/<file>.json (full text), judge/, frozen checks
/data/vlm-cache/ cached VLM answers
/data/vlm-runs/  VLM quality runs
/data/outputs/   "Parse now" outputs and the latest result per file
```

Old runs can be removed automatically: Settings > Results storage.

## Security notes

- One shared password, scrypt-hashed. Sessions are signed cookies; *Sign out all browsers* invalidates
  them all.
- The VLM proxy (port 8081) has no sign-in, because docintel calls it. The Route does not expose it.
  If you save a VLM key in the tool, enable `networkPolicy` so only the docintel test instances can
  reach it.
- Text extracted from files is passed to the judge inside tags with an instruction to treat it as data.

## Try it locally without the real services

```bash
pip install -r requirements.txt "moto[server]==5.0.5" python-docx python-pptx
moto_server -p 9100 &
python tools/mock_services.py litellm --port 9300 &
python tools/mock_services.py docintel --variant a --port 9201 --vlm http://localhost:8081/v1 &
python tools/mock_services.py docintel --variant b --port 9202 --vlm http://localhost:8081/v1 &
DATA_DIR=./data APP_SECRET_KEY=dev APP_INITIAL_PASSWORD=dev-pass-1 python -m app.main
```

Open http://localhost:8080, sign in with `dev-pass-1`, create the bucket in moto, and use
`http://localhost:9100` (keys `test`/`test`), `http://localhost:9300/v1` for the judge and VLM, and
`http://localhost:9201` and `:9202` as parsers. The mock docintel is a test double, not a parser.

Unit tests: `python -m pytest tests`.
