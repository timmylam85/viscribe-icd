# ViScribe-ICD

ViScribe-ICD is a research project for AI-assisted SOAP notes and ICD-10 coding from Vietnamese doctor–patient conversations.

## Phase 3 local development

Use Docker Compose, [uv](https://docs.astral.sh/uv/), and Python 3.12. This setup uses simulated encounters. PostgreSQL and the API listen on localhost.

1. Copy the environment template.

   ```sh
   cp -n .env.example .env
   chmod 600 .env
   ```

   Generate a separate bearer token for each test clinician with `openssl rand -hex 32`. Set `VISCRIBE_DEV_USERS` to a JSON mapping such as `{"doctor-local":"<token>"}`. If you change the PostgreSQL port or password, update `VISCRIBE_DATABASE_URL` as well.

2. Start PostgreSQL, install the API dependencies, and apply the migrations.

   ```sh
   docker compose up -d --wait
   uv sync --project viscribe-api --locked --python 3.12
   uv run --project viscribe-api --locked alembic -c viscribe-api/alembic.ini upgrade head
   ```

3. Load the sample catalog and start the API.

   ```sh
   uv run --project viscribe-api --locked python -m viscribe_api.load_catalog \
     viscribe-data/fixtures/icd10-who2019-demo.csv --version who2019-demo-v1 \
     --source-url https://icd.who.int/browse10/2019/en
   uv run --project viscribe-api --locked uvicorn viscribe_api.main:app --host 127.0.0.1 --port 8000
   ```

4. Open [Swagger UI](http://127.0.0.1:8000/docs), select **Authorize**, and enter the `doctor-local` token. Try `viêm họng` or `J02.9` in `GET /icd/search`. To create an encounter, send `{"mode":"assisted","patientRef":"demo-01"}` to `POST /encounters`. Clinicians can list and view their own encounters. Each encounter records its catalog version so later lookups use the same release.

### ICD-10 catalog

The sample fixture contains a small set of codes based on [WHO ICD-10](https://icd.who.int/browse10/2019/en). Use [Circular 06/2026/TT-BYT and the Ministry of Health appendix](https://vanban.chinhphu.vn/?classid=1&docid=217536&pageid=27160&typegroupid=6) to verify the Vietnamese research catalog. Confirm the source file, coding rules, and study code set before accepting the research data.

The importer accepts a UTF-8 CSV with `code` and `title_vi`. Optional columns are `title_en`, `chapter`, `block`, `includes`, `synonyms`, `active`, `selectable`, `in_focus_set`, `excludes_codes`, `sex_restriction`, `age_min`, `age_max`, and `rule_source`. Separate list values with `|` and specify ages in years. Set `selectable=false` for non-selectable categories or retired codes. Age, sex, and exclusion rules require a source URL. Excluded codes must exist in the same release. The importer validates the entire file before writing it. If the content changes, import a new version and update `VISCRIBE_ICD_CATALOG_VERSION` for new encounters.

Search prioritizes code and keyword matches and accepts Vietnamese text with or without diacritics. If no keyword matches are found, the API can search embeddings from the same catalog release and Azure deployment. Add `--embed` to the import command to generate missing embeddings. After an error, run the same command again to resume unfinished batches.

### Verification

```sh
uv run --project viscribe-api --locked ruff check viscribe-api
uv run --project viscribe-api --locked python -m pytest viscribe-api/tests
```

To enable the integration tests, set `VISCRIBE_TEST_DATABASE_URL` to a local PostgreSQL database with the migrations applied. The tests create and remove temporary schemas.

To check the five HIVN deployments, configure the backend `AZURE_*` variables using the HIVN guide and provide a **synthetic** PCM16 mono 24 kHz WAV file up to 15 seconds long.

```sh
uv run --project viscribe-api --locked python -m viscribe_api.smoke_azure --audio /path/to/synthetic.wav
```

The command calls each deployment once, checks Luna's structured output, and records the outcome, latency, and token usage when available in `ai_runs`. The console shows only public deployment names and statuses.

## Phase 4 audio upload and transcription

Install `ffmpeg`, `ffprobe`, and Node.js 22. Start PostgreSQL and load the sample catalog as described above. Apply the latest migration and start the API, worker, and web app in separate terminals.

```sh
uv run --project viscribe-api --locked alembic -c viscribe-api/alembic.ini upgrade head
uv run --project viscribe-api --locked uvicorn viscribe_api.main:app --host 127.0.0.1 --port 8000
```

```sh
uv run --project viscribe-api --locked python -m viscribe_api.transcription_worker
```

```sh
npm --prefix viscribe-web ci
npm --prefix viscribe-web run dev
```

The worker reads `AZURE_STT_ENDPOINT`, `AZURE_STT_API_KEY`, and `AZURE_STT_API_VERSION` from the backend configuration. Open the local app, enter the `doctor-local` token from `VISCRIBE_DEV_USERS` in `.env`, create an encounter, and select a simulated recording. The interface shows progress by chunk and displays the final transcript when ready. The same upload, queue, status, and retry operations are available in Swagger UI. [Open ViScribe locally](http://127.0.0.1:5173/)

The default upload limits are 64 MiB and 30 minutes. Supported formats are WAV, MP3, M4A, MP4, and WebM with audio. The worker converts each upload to PCM16 mono 16 kHz WAV, divides it into chunks of up to 120 seconds, and joins the transcripts in order. The timestamps shown in the interface mark chunk boundaries. Recordings and transcripts for simulated encounters live in local PostgreSQL, are tied to an upload revision, and are accessible to the clinician who owns the encounter.

If Azure returns `429` or a temporary connection error, the worker schedules a retry and retains completed chunks. The PostgreSQL queue preserves jobs across worker restarts. The default minimum interval between STT requests is 6.2 seconds per deployment. Repeating the current upload reuses its recording and job. Uploading a different file starts a new revision and preserves the earlier result in its original revision.

## Language conventions

Write source identifiers, comments, docstrings, commit messages, and this README in English. Keep Vietnamese interface text in `viscribe-web/src/locales/vi.json`. Keep Vietnamese sample data where it exercises the language the application supports.
