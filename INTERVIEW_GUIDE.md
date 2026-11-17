# Interview guide

Reference sheet for demos and interviews.

## 1. How it works

1. A PDF or image (invoice or purchase order) arrives at `POST /extract` or through the
   web page at `/`.
2. `app/rendering.py` sniffs magic bytes, renders pages to PNG with pypdfium2 and reads
   the PDF text layer if there is one.
3. Mode: **text** sends the text layer to llama3.2; **vision** sends page images to
   qwen2.5vl:3b; **auto** picks text when the PDF has at least 40 characters of text
   (`MIN_TEXT_LAYER_CHARS`), otherwise vision.
4. The model returns one JSON object in a fixed shape (`app/prompts.py`).
5. `app/schemas.py` validates it with Pydantic: types, required fields, then cross-field
   rules (amount = qty x unit price, lines sum to subtotal, subtotal + tax = total,
   plausible dates, due/delivery date not before issue date).
6. On failure, `app/pipeline.py` sends the model its own answer plus the exact error list
   (with paths such as `line_items.2.amount`) and asks for a fix, up to `MAX_ATTEMPTS`.
7. Every result, valid or not, is stored with each attempt's errors and latency. Only
   validated documents fill the typed SQL columns and line-item table.
8. `GET /export.xlsx` writes a Documents sheet and a Line items sheet.
9. `scripts/eval.py` runs the same pipeline on 18 synthetic labelled documents.

Providers: `ollama` (default), `claude` (Anthropic SDK), `mock` (replays recorded
llama3.2 / qwen2.5vl answers keyed by file SHA-256; used by tests and CI).

## 2. Key files, in reading order

1. `app/schemas.py` - the contract and the business rules
2. `app/pipeline.py` - the validate-and-retry loop (about 165 lines)
3. `app/prompts.py` - system prompt and retry message
4. `app/providers/` - `base.py`, then `ollama.py`, `mock.py`, `claude.py`
5. `app/rendering.py` - file sniffing, page rendering, text layer
6. `app/main.py` - routes; `create_app(settings, provider)` factory for tests
7. `app/storage.py`, `app/exporters.py` - SQL and Excel
8. `app/evaluation.py`, `scripts/eval.py` - metrics and the eval runner
9. `tests/test_pipeline.py` - the retry loop with a scripted provider

## 3. Likely questions

**Why validate instead of trusting JSON mode?**
Ollama's `format: "json"` only guarantees syntax. It does not stop the model from
putting the tax rate in the tax field or dropping a line. Invoices are self-checking
(lines sum to subtotal, subtotal + tax = total), so arithmetic rules catch those errors.

**Why feed the errors back rather than just retrying?**
At temperature 0 a blind retry usually returns the same answer. The retry message names
the failing field and the numbers involved, which gives the model something to fix. The
loop keeps the last parseable answer, marked invalid, so a person can review it.

**Did the retry actually help?**
Answer from `docs/eval-results.md`: the per-document table shows retries and whether
the final answer was valid. On CPU each retry of a multi-page document costs minutes,
and some documents stay invalid after 3 attempts, so it helps on some, not all.

**What do the eval numbers mean, and their limits?**
Field accuracy compares 10 header fields after normalisation (amounts within 0.01);
line items use one-to-one matching and F1. The documents are synthetic and the subsets
are small (8 text, 5 vision), so one document moves a figure by 10-20 points. They show
how the pipeline handles these layouts, not real-world accuracy.

**Why is schema-valid not the same as correct?**
A document can satisfy every rule and still swap vendor and buyer or misread a date.
That is why field accuracy is scored separately from the schema-valid rate.

**Why both text and vision modes?**
Text mode gives the model exact characters and is faster on CPU. Scans have no text
layer, so they need a vision model.

**How do tests run without a model?**
A scripted fake provider drives the retry loop; Ollama and Claude adapters run against
`httpx.MockTransport`; API tests use the mock provider with recordings written by the
test. CI also replays the recorded llama3.2 answers through `scripts/eval.py`.

**Is the Claude provider tested?**
Only against a stubbed HTTP layer (request shape and error mapping). There was no API
key on the build machine, so it is not verified against the live API.

**How would you add a new document type?**
Add a Pydantic model to the discriminated union in `schemas.py` (keyed by
`document_type`), describe it in the prompt, add labelled samples, extend the eval.

**What would change for production use?**
Background jobs instead of a blocking request, authentication, object storage for
uploads, Alembic migrations instead of `create_all`, a review queue for invalid
documents, and a larger eval set with real, consented documents.

## 4. Three small improvements

1. **Show each attempt's errors in the UI.** Start in `app/static/app.js`,
   `showResult`: the attempt list is rendered from `doc.attempts`; add each
   attempt's `errors` beneath it.
2. **Flag ambiguous dates.** In `app/pipeline.py` (or a validator in `schemas.py`),
   flag documents where swapping day and month in the issue date would also pass the
   date rules.
3. **CSV export.** Add `build_csv(documents)` to `app/exporters.py` with the `csv`
   module, a `GET /export.csv` route in `app/main.py` modelled on `export_xlsx`, and a
   test in `tests/test_api.py`.
