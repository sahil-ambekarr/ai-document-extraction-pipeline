# Interview guide

Notes for explaining this project in an interview. Written for someone who knows
JavaScript, C# and SQL well and is still getting comfortable with Python.

## 1. How it works, in simple words

1. A user uploads a PDF or an image (an invoice or a purchase order) to a FastAPI
   endpoint, `POST /extract`, or through the small web page at `/`.
2. `app/rendering.py` checks the file's magic bytes (not the filename), renders each
   PDF page to a PNG with pypdfium2, and pulls out the PDF's text layer if there is
   one. An image upload becomes a single "page".
3. The pipeline picks a mode. **Text mode** sends the text layer to a text model
   (llama3.2). **Vision mode** sends the page images to a vision model
   (qwen2.5vl:3b). `auto` uses text when the PDF has a text layer and vision for scans.
4. The model is asked for one JSON object in a fixed shape (`app/prompts.py`).
5. `app/schemas.py` validates that JSON with Pydantic: types and required fields, then
   business rules - each line's amount equals quantity x unit price, line amounts add
   up to the subtotal, subtotal + tax = total, dates are plausible and the due or
   delivery date is not before the issue date.
6. If validation fails, `app/pipeline.py` sends the model its own answer back plus the
   exact list of errors ("line_items.2.amount: ...", "subtotal + tax = 545.14 but total
   is 564.17") and asks for a corrected JSON. This repeats up to `MAX_ATTEMPTS` times.
7. The result - valid or not - is saved with SQLAlchemy (SQLite by default) together with
   every attempt's errors and latency. Only validated documents fill the typed columns
   and the line item table, so SQL consumers can trust them.
8. `GET /export.xlsx` builds an Excel file with openpyxl: a "Documents" sheet and a
   "Line items" sheet.
9. `scripts/eval.py` runs the same pipeline over 18 synthetic labelled documents and
   scores every field against the ground truth.

The model behind step 4 is swappable: `LLM_PROVIDER=ollama` (local), `claude`
(Anthropic SDK) or `mock` (replays recorded model answers so tests and CI need no model).

## 2. Files to read first, in this order

1. `app/schemas.py` - the contract. Everything else exists to fill these models.
2. `app/pipeline.py` - the retry loop; about 160 lines and the heart of the project.
3. `app/prompts.py` - what the model is actually told, including the retry message.
4. `app/providers/base.py`, then `ollama.py`, `mock.py`, `claude.py` - the provider
   interface (like a C# interface with three implementations).
5. `app/rendering.py` - PDF and image handling.
6. `app/main.py` - FastAPI routes. `create_app()` is a factory so tests can pass their own
   settings and provider (similar to configuring a `WebApplicationFactory` in ASP.NET).
7. `app/storage.py` and `app/exporters.py` - SQL and Excel.
8. `app/evaluation.py` and `scripts/eval.py` - how accuracy is measured.
9. `tests/test_pipeline.py` - shows the retry loop with a fake provider.

Python notes that help when reading: `@dataclass` is like a C# record with an
auto-generated constructor; `X | None` means nullable; `Annotated[T, ...]` attaches
metadata (validators, FastAPI parameter info) to a type; `model_validator(mode="after")`
runs after all fields are parsed, which is where cross-field rules go.

## 3. Likely questions and honest answers

**Why validate with Pydantic instead of trusting the model's JSON mode?**
JSON mode (Ollama's `format: "json"`) only guarantees syntactically valid JSON. It does
not stop the model from putting the tax rate in the tax field or dropping a line item.
The arithmetic checks catch exactly those mistakes, because an invoice is
self-checking: lines must add up to the subtotal and subtotal + tax must equal the total.

**How does the retry loop work, and why feed the errors back?**
`run_extraction` keeps the whole conversation. After a failed attempt it appends the
model's answer as an `assistant` message and a `user` message listing each validation
error with its path. A blind retry at temperature 0 would usually return the same
answer; telling the model which number is wrong gives it something to fix. The loop
stops at `MAX_ATTEMPTS` and keeps the last parseable answer, marked invalid, so a person
can review it. The eval reports how many documents needed a retry and whether the
retry helped.

**What do the eval numbers mean, and what are their limits?**
They come from `scripts/eval.py` on 18 synthetic documents I generated, so they say how
well the pipeline reads these three layouts, not real-world invoices. Header fields are
compared after normalisation (case, spaces, punctuation), amounts with a 0.01 tolerance,
and line items with a one-to-one match and F1. The set is small, so one document moves
an accuracy figure by several points. The numbers are in `docs/eval-results.md`.

**Why is "schema-valid" not the same as "correct"?**
A document can pass every rule and still be wrong - for example, if the model swaps
the vendor and the buyer, or reads 05/01 as 1 May instead of 5 January. Validation
catches internal inconsistency, not every misreading. That is why the eval scores
fields against ground truth separately from the schema-valid rate.

**How would you handle a document type you have not seen?**
The discriminated union in `schemas.py` (`document_type` picks Invoice or
PurchaseOrder) is the extension point: add a model, add it to the union, describe it in
the prompt, add labelled samples and extend the eval. A `doc_type` hint can also be
passed per request when the caller already knows the type.

**Why text mode and vision mode?**
Text mode is faster and more accurate when a PDF has a real text layer, because the
model gets the exact characters. Scans and photos have no text layer, so they need a
vision model. `auto` chooses based on how much text the PDF actually has
(`MIN_TEXT_LAYER_CHARS` in `rendering.py`).

**How do the tests run without a model?**
Three ways: `tests/test_pipeline.py` uses a scripted fake provider (first answer invalid,
second valid) to test the loop; the Ollama and Claude adapters run against stubbed HTTP
transports (`httpx.MockTransport`); and the API tests use the mock provider with
recordings written by the test itself. CI also replays real llama3.2 answers that
`scripts/eval.py --record` saved in `data/mock_responses/`.

**Is the Claude provider tested?**
Only against a stubbed HTTP layer that checks the request body (system prompt, image
blocks, text) and the error mapping. I had no API key on the build machine, so it is
not verified against the live API, and the README says so.

**What would you change for production?**
Run extraction in a background worker with a job id instead of holding the HTTP
request open for minutes; add authentication; store uploads in object storage; add
database migrations (Alembic) instead of `create_all`; add a human review queue for
invalid documents; and grow the eval set with real, consented documents.

**Why SQLite by default, and does SQL Server work?**
SQLite needs no setup and makes tests self-contained. The storage layer only uses
SQLAlchemy, so `DATABASE_URL` can point at SQL Server via pyodbc. I did not have SQL
Server or Docker locally, so that path is not verified.

## 4. Three small improvements to make yourself

1. **Show the attempt history in the UI.** The API already returns each attempt's
   errors. Start in `app/static/app.js`, function `showResult`: render
   `doc.attempts[i].errors` under each attempt chip, so a reviewer can see what the
   model fixed on the retry.
2. **Add a confidence flag for ambiguous dates.** Dates like 05/01/2026 can be read two
   ways. Start in `app/schemas.py`: add an optional field the model can fill
   (`date_format_guess`), or in `app/pipeline.py` compare the issue date and the
   due date and flag cases where swapping day and month would also be valid.
3. **Export one document per CSV for SQL bulk import.** Start in `app/exporters.py`:
   add `build_csv(documents)` using Python's built-in `csv` module, then add a
   `GET /export.csv` route in `app/main.py` modelled on `export_xlsx`, and a test in
   `tests/test_api.py`.
