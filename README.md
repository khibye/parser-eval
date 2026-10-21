# parser-eval

**English** | [עברית](README.he.md)

parser-eval is a web tool for comparing document parsers end to end, the way Open WebUI uses them.
Every file in an S3 folder is sent to two or more parser instances (for example docintel with Xberg
1.0.14 and docintel with Xberg 1.3.10). The tool then shows which files got better, which got worse,
what exactly changed in the text, and how long each parser took.

Use it before every parser or Xberg upgrade, and whenever you want to try a different parser behind
docintel.

![Comparison of two parser versions](docs/images/compare.png)

## Contents

- [How it works](#how-it-works)
- [Install on OpenShift](#install-on-openshift)
- [First setup](#first-setup)
- [Using the app](#using-the-app)
  - [1. Add test files](#1-add-test-files)
  - [2. Write checks](#2-write-checks)
  - [3. Start a run](#3-start-a-run)
  - [4. Read the results](#4-read-the-results)
  - [5. Look at one file](#5-look-at-one-file)
  - [6. Download a report](#6-download-a-report)
  - [7. Measure VLM quality](#7-measure-vlm-quality)
- [Recipe: before upgrading Xberg](#recipe-before-upgrading-xberg)
- [Settings reference](#settings-reference)
- [Data, backups and retention](#data-backups-and-retention)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## How it works

```
             S3 bucket (test files)
                     │
               parser-eval  ──────────────►  judge model (LiteLLM)
              │           │
   POST /v1/convert/file  POST /v1/convert/file
              ▼           ▼
       docintel A      docintel B       (one instance per parser version)
              │           │
              └─ VLM calls ─►  parser-eval VLM cache (port 8081)  ──►  LiteLLM
```

A few terms used throughout the app:

| Term | Meaning |
|---|---|
| **Parser** | One docintel instance, with its own backend (an Xberg version, Docling, and so on). |
| **Run** | One pass over a folder of files, sending every file to every selected parser in parallel. |
| **Baseline (A)** | The parser you compare against, usually the one in production. |
| **Candidate (B)** | The parser you are considering. A run can have several candidates. |
| **Check** | A small pass/fail statement about a file's output, for example "this sentence must appear". |
| **Judge** | An LLM that answers question checks, picks the better output when the text changed a lot, suggests checks, and scores VLM descriptions. |
| **VLM cache** | A proxy in front of LiteLLM. docintel's VLM calls go through it, so both parsers get the same answer for the same image and the comparison shows only what the parser changed. |

The tool sends each file exactly as Open WebUI sends it to Docling: a multipart `POST` to
`/v1/convert/file`, with your Docling parameters as form fields, and it reads the text from
`document.md_content`. What you see in the tool is what Open WebUI would get.

Everything is stored as files on a PVC: settings, parsers, checks, every output of every run, and
reports. Nothing is lost when the pod restarts, and a run that was cut off can be resumed.

## Install on OpenShift

### 1. Build the image

The base image is `python:3.11-slim`. The Python packages are pinned to early-2024 releases (see
`requirements.txt`) so an older internal mirror will have them. Nothing is downloaded at run time.

```bash
podman build -t parser-eval:1.0.0 \
  --build-arg BASE_IMAGE=<your-registry>/python:3.11-slim \
  --build-arg PIP_INDEX_URL=https://<your-mirror>/simple \
  --build-arg PIP_TRUSTED_HOST=<your-mirror-host> .
podman push parser-eval:1.0.0 <your-registry>/eval/parser-eval:1.0.0
```

The image runs as any user ID in group 0 and with a read-only root filesystem, as OpenShift requires.

### 2. Seal the secrets

The chart reads two secrets from a SealedSecret:

| Key | Purpose |
|---|---|
| `APP_INITIAL_PASSWORD` | Password for the first sign-in. You change it later in the app. |
| `APP_SECRET_KEY` | Random key that encrypts the passwords and API keys you save in Settings, and signs sessions. |

Create both with the script. It asks for the first password, generates the key, and prints a values
snippet with both values sealed for your namespace:

```bash
./scripts/seal-secrets.sh <namespace> parser-eval-secrets <controller-namespace> <controller-name>
```

Save the output as `values-prod.yaml`.

> Do not change `APP_SECRET_KEY` after the first start. If it changes, the keys saved in Settings
> cannot be decrypted and you have to type them again.

### 3. Install the chart

```bash
helm upgrade --install parser-eval chart/parser-eval -n <namespace> \
  -f values-prod.yaml \
  --set image.repository=<your-registry>/eval/parser-eval \
  --set image.tag=1.0.0
```

What the chart creates:

| Object | Notes |
|---|---|
| Deployment | One replica, `Recreate` strategy, because the data lives on one ReadWriteOnce volume. |
| Service | Port 8080 for the UI, port 8081 for the VLM cache. |
| Route | Only port 8080, with edge TLS by default. The VLM cache stays inside the cluster. |
| PVC | 20Gi by default. Kept on `helm uninstall`. |
| SealedSecret | Kept on `helm uninstall`. |
| NetworkPolicy | Optional (`networkPolicy.enabled`), limits who can reach the VLM cache port. |

Common values to change: `persistence.size`, `persistence.storageClass`, `route.host`,
`networkPolicy.enabled`, `resources`. All values are documented in `chart/parser-eval/values.yaml`.

## First setup

Open the Route address, sign in with the password you sealed, and go through Settings once.

![Settings](docs/images/settings.png)

1. **Settings > Access.** Change the password.
2. **Settings > File storage.** Enter the S3 endpoint, bucket, access key and secret key. Most internal
   S3 services need *Path style* addressing. If the endpoint uses an internal CA, paste the CA
   certificate (or load it from a file). Press **Test connection**: it lists the bucket and writes and
   deletes a small test object, because the app needs to read and write.
3. **Settings > Judge.** Enter the LiteLLM URL (for example `http://litellm.<ns>.svc:4000/v1`), the
   model name and the API key. Tick **This model can see images** if it can. Press **Test judge**.
4. **Settings > VLM cache.** Enter the LiteLLM URL that VLM calls should be forwarded to. Copy the
   address shown at the top of this tab.
5. **In every docintel instance you want to evaluate,** set the VLM URL to that address
   (`http://parser-eval.<namespace>.svc:8081/v1`). Keep docintel's own LiteLLM key; the cache passes it
   through.
6. **Parsers.** Add one parser per docintel instance (see below).
7. **Files & tests > Add sample set** gives you 23 ready-made test files to start with.

### Adding a parser

Go to **Parsers > Add parser** and fill in:

| Field | What to enter |
|---|---|
| Name | A short name you will recognize in results, for example `xberg-1.3.10`. |
| docintel URL | The service address, for example `http://docintel-x1310.eval.svc:8080`. The tool adds `/v1/convert/file`. |
| Authentication | None, a bearer token, or a custom header, if docintel needs one. |
| Docling parameters | The same JSON you use for `DOCLING_PARAMS` in Open WebUI. Each key is sent as a form field. |
| Text in the response | Where the text is in the JSON answer. Default `document.md_content`. |
| Send lists as | Repeated form fields (default, like docling-serve expects) or one JSON string. |
| Timeout, Max parallel | Per-request timeout and how many requests this instance gets at once. |

Press **Test connection**. It sends a small PDF and shows how long it took and how much text came back.
**Duplicate** is the quickest way to add another version: copy, rename, change the URL.

## Using the app

### 1. Add test files

Open **Files & tests**. Files live in your S3 bucket, organized in folders. A folder is what you
select when you start a run, so a structure like this works well:

```
corpus/
  synthetic/      the bundled sample set
  real/
    scans/
    office/
    email/
```

- **Upload files** or drag files onto the page. They go to the folder you have open.
- **New folder** creates a folder in S3.
- **Add sample set** uploads the bundled synthetic files with their checks. They cover cases that broke
  before: Hebrew OCR, text in page margins, mixed-direction lines, a deck with seven different images,
  a logo repeated on every slide, white text on black, a transparent stamp, legacy `.doc` and `.ppt`,
  an email with attachments, a 40-page report, a tiny icon and a blank image.
- The table shows how many approved checks and drafts each file has, the result of the latest run, and
  **File changed** when the file in S3 was replaced after its checks were written.

Click a file to open it in the test editor.

### 2. Write checks

A check is one thing that must be true about a file's output. Today you probably open the output and
look for things by eye. A check is writing down what you look for, so the tool checks it on every
version, every time.

![Test editor](docs/images/test-editor.png)

The test editor shows the file's output on the left (or the original file, on the **Original** tab) and
its checks on the right. The mark next to each check shows whether it passes against the output you
have selected at the top.

**Getting an output to look at.** Pick one in **Output from** (any earlier run), or choose a parser in
**Parse now with** and press **Parse now**.

**Check types:**

| Type | Passes when | Good for |
|---|---|---|
| Contains | The text appears. Close match (90% similar by default) or exact. | Key sentences, numbers, names. |
| Not contains | The text does not appear. | Leftover placeholders such as `<!-- image -->`, known garbage. |
| Order | The parts appear in this order. One part per line. | Headings, reading order, mixed-direction lines. |
| Appears once | The text appears exactly once. | Duplicated OCR text, repeated slides. |
| Image blocks | The output has exactly (or at least) N image analysis blocks. | Images that were dropped or duplicated. |
| Image contains | Image block number N contains the text. | Image analysis attached to the wrong place. |
| Table cell | One table row, or one line, holds both the label and the value. | Tables, forms with label and value. |
| Question | The judge answers the question from the output only, and the answer must match yours. | Facts that can be worded in different ways. |

Before comparing, both sides are normalized: niqqud, direction marks, quote styles (׳ ״ ' ") and extra
spaces are removed, and letters are compared without case.

**Three ways to add checks:**

1. **Add check** and fill in the form.
2. **Select text** in the output. A button appears that makes a *Contains* check from the selection.
3. **Suggest checks.** The judge reads the output and proposes 6 to 10 checks as drafts. Drafts are
   built from the output, so they also copy its mistakes. Open the **Original** tab, compare, and
   approve only the drafts that are true for the real file. Discard the rest.

**How many checks?** Five to ten per file is enough. Cover what matters most in that file: the title,
one or two key sentences, the order of the main sections, a table value, the number of images, and one
question. A check that fails for a correct output is worse than no check, so prefer close match for
OCR text.

**When a file changes in S3,** its checks are flagged. Look them over, then press
**mark them as current**.

Each run freezes the checks as they were when it started, so editing checks later does not change past
results.

### 3. Start a run

Go to **New run**.

![New run](docs/images/new-run.png)

| Section | What to choose |
|---|---|
| Run name | Something you will recognize later, for example `xberg 1.3.10 vs 1.0.14`. |
| Files | The S3 folder (all subfolders are included), the file types, and optionally only files that have approved checks. |
| Parsers | One **Baseline** and one or more parsers to **Compare**. The health column shows whether each one answers. |
| VLM answers | **Replay from cache** (recommended): an image the cache has seen gets the stored answer, new images go to the VLM once. **Live**: every image goes to the VLM, so answers can differ between runs. **Refresh cache**: call the VLM again and replace stored answers. |
| Judge | **Answer question checks**. **Pick the better output** for files whose text changed by more than the threshold. **Show the judge the original pages** (PDFs and images, slower, needs a vision model). |
| Speed | Parallel requests per parser, timeout per file, and how many times to send each file (3 gives steadier timings). |

The bar at the bottom shows how many files, requests and checks the run will have. **Save as preset**
keeps these choices for next time; **Run again** on a finished run does the same.

Runs are queued and run one at a time, each highly parallel inside. You can leave the page; the run
continues on the server. If the pod restarts, the run is marked *Interrupted* and **Resume** continues
from the last finished file.

### 4. Read the results

Open a run from **Runs**. At the top, **A** and **B** choose which two parsers to compare (useful when a
run has several candidates).

The summary:

| Tile | Meaning |
|---|---|
| Checks passed | Share of checks B passes, and the difference from A. |
| Files ▲ ▼ | Files that improved and regressed. |
| Needs review | Files whose checks did not change, but whose text changed by more than the threshold. |
| Judge picks | For files over the threshold, how often each side was judged better (asked in both orders). |
| Median time | Median seconds per file for B, with A and the 95th percentile. |
| Errors | Failed requests and timeouts. |

A file is **regressed** when B passes fewer of its checks than A, or B failed where A did not.
**Improved** is the opposite.

The tabs below the summary:

- **By file type:** checks, changes, timing and errors for PDF, DOCX, PPTX, images and so on.
- **Regressions:** the files to fix before upgrading. The details column lists the checks that now fail.
- **Needs review:** the text changed a lot but no check noticed. Look at these by hand, and add checks
  for what you find.
- **Improvements** and **All files**.

### 5. Look at one file

Click a file in any list.

![One file](docs/images/file-detail.png)

- **Side by side:** A and B aligned line by line. Red is only in A, green only in B, yellow changed.
  **Only changed parts** hides long unchanged stretches.
- **Inline diff**, **Raw A**, **Raw B:** other views of the same outputs.
- **Checks:** every check with its result for A and B, and why it failed. For question checks you see
  the judge's answer.
- **Judge:** the verdict, its reason, and the reason from the swapped order.
- **Edit checks** opens the file in the test editor; **Download original** downloads it from S3.

### 6. Download a report

**Download report** on a run saves one HTML file with the summary, the table by file type, every
regression and file that needs review with its text diff, and the full file list. It opens in any
browser without the app, so you can attach it to a ticket or an upgrade request.

### 7. Measure VLM quality

**VLM quality** is a separate flow for the VLM itself, independent of the parser.

![VLM quality](docs/images/vlm-quality.png)

1. Put images in an S3 folder. Use subfolders for kinds of images (`charts/`, `scans/`, `photos/`); the
   results are grouped by them.
2. Open **VLM quality > New VLM run**. Choose the folder and add two or more setups: a name, the LiteLLM
   model, the prompt, the size limit for the long side, and whether to put white under transparency.
3. Each setup describes every image through LiteLLM. When the judge can see images, it scores each
   description from 1 to 5 on accuracy, completeness, reading the text, not inventing details, and
   overall. It sees one description at a time and does not know which setup wrote it.
4. **Overview** shows average scores, head-to-head counts and scores by image kind. **Image by image**
   shows the image next to every description and its scores, with filters such as *Where the second
   setup is worse*.

## Recipe: before upgrading Xberg

1. Deploy a second docintel that uses the new Xberg version, next to the current one. Point both
   docintel VLM URLs at the parser-eval VLM cache.
2. Add both as parsers. Duplicate the current one and change the URL and name.
3. Start a run on your whole test folder: baseline = current, candidate = new, **Replay from cache**,
   judge on.
4. Read **Regressions** first. Every regression is a reason not to upgrade yet, or a check that needs
   updating because the new output is actually better.
5. Read **Needs review**, and add checks for what you find there.
6. Download the report and attach it to the upgrade decision.
7. After fixing configuration (for example OCR language or image settings), run again. With
   **Replay from cache** the VLM answers stay the same, so the difference is the parser alone.

## Settings reference

| Tab | Fields |
|---|---|
| Access | Change password, how long sign-in lasts, sign out all browsers. |
| File storage | S3 endpoint, bucket, root folder (only files under it are used), access key, secret key, region, addressing style, TLS verification, internal CA certificate. The CA is also used for docintel, LiteLLM and the judge. |
| Judge | LiteLLM URL, model, API key, context limit in tokens (long documents are trimmed in the middle to fit), parallel judge calls, temperature, timeout, whether it can see images. |
| VLM cache | The address for docintel, the LiteLLM URL to forward to, which key to use (the one docintel sends, or one saved here), timeout, statistics, Clear cache. The saved key is also used by VLM quality runs (or the judge key if empty). |
| Evaluation | The tag docintel wraps image analysis in (default `image_analysis`), the default change threshold for the judge and *Needs review*, and the close-match threshold. |
| Results storage | Disk usage of the PVC by part, and how long to keep run outputs. |

Passwords and API keys are stored encrypted with `APP_SECRET_KEY`, and are never sent back to the
browser. A field that shows *Saved · type to replace* keeps its value unless you type a new one.

## Data, backups and retention

```
/data/config/     settings.json, parsers.json, auth.json, presets.json, ca.pem
/data/checks/     one JSON file per S3 file
/data/runs/       one folder per run: run.json, results/<parser>/<file>.json (full text),
                  judge/, and the checks frozen at the start of the run
/data/vlm-cache/  cached VLM answers
/data/vlm-runs/   VLM quality runs
/data/outputs/    "Parse now" outputs and the latest result per file
```

To back up, copy `/data` (for example with a VolumeSnapshot or `oc rsync`). Every file is plain JSON
and is written atomically, so a copy taken while the app runs is consistent per file.

**Settings > Results storage > Keep run outputs** removes old runs automatically. *Delete older runs
now* applies it immediately. Checks, settings and the VLM cache are never removed automatically.

## Security

- One shared password, stored as an scrypt hash. After a wrong password, further attempts from the same
  address are slowed down.
- Sessions are signed cookies, marked Secure when the Route uses TLS. **Sign out all browsers**
  invalidates every session.
- The VLM cache on port 8081 has no sign-in, because docintel calls it. The Route does not expose it.
  If you save a VLM key in the tool, enable `networkPolicy` in the chart so only the docintel test
  instances can reach it.
- Text extracted from files is passed to the judge inside tags, with an instruction to treat it as
  data and not as instructions.

## Troubleshooting

| Symptom | What to check |
|---|---|
| *S3 is not set up yet* | Settings > File storage, then Test connection. |
| S3 test fails with a certificate error | Paste the internal CA in Settings > File storage, or untick Verify TLS for a test. |
| S3 test fails with *SignatureDoesNotMatch* or *NoSuchBucket* | Usually addressing style: try *Path style*. Check the region. |
| A parser shows *Not reachable* | The URL must be reachable from the parser-eval pod. Try `oc rsh` into the pod and `curl` the URL. |
| Every file fails with *No text at document.md_content* | The response has a different shape. Look at one response and set *Text in the response*. |
| *HTTP 422* from docintel | A Docling parameter has the wrong format. Try *Send lists as: JSON text*. |
| VLM cache shows 0% from cache | docintel is not using the cache address as its VLM URL, or the images differ in pixels between versions. |
| Timeouts on large files | Raise the timeout in the parser and in the run, and check the Route timeout (`haproxy.router.openshift.io/timeout`). |
| Judge errors in results | Settings > Judge > Test judge. Check the model name in LiteLLM and the context limit. |
| A run shows *Interrupted* | The pod restarted. Press Resume; finished files are kept. |

The pod log (`oc logs deploy/parser-eval`) shows failed jobs with their stack traces.

## Development

```
app/        FastAPI backend: UI and API on 8080, VLM cache on 8081
static/     Browser UI, plain HTML, CSS and JavaScript, no build step, nothing loaded from the internet
samples/    The synthetic test set and its checks
chart/      Helm chart for OpenShift
scripts/    seal-secrets.sh
tools/      gen_samples.py rebuilds the samples; mock_services.py fakes docintel and LiteLLM
tests/      Unit tests
```

Run everything locally with fake services:

```bash
pip install -r requirements.txt "moto[server]==5.0.5" python-docx python-pptx
moto_server -p 9100 &
python tools/mock_services.py litellm --port 9300 &
python tools/mock_services.py docintel --variant a --port 9201 --vlm http://localhost:8081/v1 &
python tools/mock_services.py docintel --variant b --port 9202 --vlm http://localhost:8081/v1 &
DATA_DIR=./data APP_SECRET_KEY=dev APP_INITIAL_PASSWORD=dev-pass-1 python -m app.main
```

Open http://localhost:8080 and sign in with `dev-pass-1`. Create the bucket in moto first
(`aws --endpoint-url http://localhost:9100 s3 mb s3://eval-corpus`, keys `test`/`test`). Then use
`http://localhost:9100` for S3, `http://localhost:9300/v1` for the judge and the VLM, and
`http://localhost:9201` and `http://localhost:9202` as two parsers. The mock docintel is a test double,
not a parser: variant b differs from variant a on purpose, so a comparison has something to show.

Unit tests: `python -m pytest tests`. To rebuild the sample set (needs LibreOffice and a Hebrew font):
`python tools/gen_samples.py`.
