"""FastAPI app: upload -> render -> extract (validate + retry) -> store -> browse / export."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from .api_models import DocumentDetail, DocumentSummary, HealthOut, to_detail, to_summary
from .config import Settings, get_settings
from .exporters import build_workbook
from .logging_config import configure_logging
from .pipeline import ExtractionInputError, run_extraction
from .providers import LLMProvider, ProviderError, build_provider
from .rendering import RenderedDocument, UnsupportedDocumentError, render_document
from .storage import (
    documents_for_export,
    get_document,
    list_documents,
    make_engine,
    save_extraction,
)

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _page_dir(settings: Settings, sha256: str) -> Path:
    return settings.storage_dir / sha256


def _store_pages(settings: Settings, rendered: RenderedDocument) -> None:
    folder = _page_dir(settings, rendered.sha256)
    folder.mkdir(parents=True, exist_ok=True)
    for number, png in enumerate(rendered.pages, start=1):
        (folder / f"page-{number}.png").write_bytes(png)


def _stored_page_count(settings: Settings, sha256: str) -> int:
    folder = _page_dir(settings, sha256)
    return len(list(folder.glob("page-*.png"))) if folder.is_dir() else 0


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def create_app(settings: Settings | None = None, provider: LLMProvider | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = make_engine(settings.database_url)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    llm = provider or build_provider(settings)

    app = FastAPI(
        title="AI Document Extraction Pipeline",
        version="0.1.0",
        description=(
            "Extract invoices and purchase orders from PDFs or images with an LLM, validate "
            "the output against Pydantic schemas, retry with the validation errors as "
            "feedback, and export to Excel and SQL."
        ),
    )
    app.state.settings = settings
    app.state.provider = llm
    app.state.session_factory = session_factory

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/health", response_model=HealthOut, tags=["meta"])
    def health() -> HealthOut:
        reachable: bool | None = None
        detail = None
        if llm.name == "ollama":
            try:
                httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=3.0).raise_for_status()
                reachable = True
            except httpx.HTTPError as exc:
                reachable, detail = False, f"Ollama not reachable: {exc!r}"
        return HealthOut(
            status="ok" if reachable is not False else "degraded",
            provider=llm.name,
            extraction_mode=settings.extraction_mode,
            max_attempts=settings.max_attempts,
            models={"text": llm.model_for("text"), "vision": llm.model_for("vision")},
            database=engine.dialect.name,
            provider_reachable=reachable,
            detail=detail,
        )

    @app.post(
        "/extract",
        response_model=DocumentDetail,
        tags=["extraction"],
        responses={
            413: {"description": "File too large"},
            415: {"description": "Not a PDF, PNG or JPEG"},
            422: {"description": "Mode cannot be used for this file"},
            502: {"description": "The LLM provider failed"},
        },
    )
    async def extract(
        session: SessionDep,
        file: Annotated[UploadFile, File(description="PDF, PNG or JPEG")],
        mode: Annotated[
            Literal["auto", "text", "vision"] | None,
            Form(description="Overrides EXTRACTION_MODE for this request"),
        ] = None,
        doc_type: Annotated[
            Literal["invoice", "purchase_order"] | None,
            Form(description="Optional hint; otherwise the model decides"),
        ] = None,
    ) -> DocumentDetail:
        limit = settings.max_upload_mb * 1024 * 1024
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB")
        try:
            rendered = render_document(data, settings.max_pages, settings.vision_max_side)
        except UnsupportedDocumentError as exc:
            raise HTTPException(415, str(exc)) from exc
        _store_pages(settings, rendered)
        try:
            # Sync and potentially minutes long on a CPU; keep it off the event loop.
            result = await run_in_threadpool(
                run_extraction, rendered, llm, mode or settings.extraction_mode,
                settings.max_attempts, doc_type,
            )
        except ExtractionInputError as exc:
            raise HTTPException(422, str(exc)) from exc
        except ProviderError as exc:
            logger.error("provider failed", extra={"doc": rendered.sha256[:12], "err": str(exc)})
            raise HTTPException(502, str(exc)) from exc
        record = save_extraction(session, file.filename or "upload", rendered, result)
        return to_detail(record, len(rendered.pages))

    @app.get("/documents", response_model=list[DocumentSummary], tags=["documents"])
    def documents(
        session: SessionDep,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[DocumentSummary]:
        return [to_summary(r) for r in list_documents(session, limit, offset)]

    @app.get("/documents/{document_id}", response_model=DocumentDetail, tags=["documents"])
    def document(document_id: int, session: SessionDep) -> DocumentDetail:
        record = get_document(session, document_id)
        if record is None:
            raise HTTPException(404, "Document not found")
        return to_detail(record, _stored_page_count(settings, record.sha256))

    @app.get(
        "/documents/{document_id}/pages/{page}",
        tags=["documents"],
        response_class=FileResponse,
        responses={200: {"content": {"image/png": {}}}},
    )
    def page_image(document_id: int, page: int, session: SessionDep) -> FileResponse:
        record = get_document(session, document_id)
        if record is None:
            raise HTTPException(404, "Document not found")
        path = _page_dir(settings, record.sha256) / f"page-{page}.png"
        if page < 1 or not path.is_file():
            raise HTTPException(404, "Page not found")
        return FileResponse(path, media_type="image/png")

    @app.get(
        "/export.xlsx",
        tags=["export"],
        responses={200: {"content": {XLSX_MEDIA_TYPE: {}}}},
    )
    def export_xlsx(
        session: SessionDep,
        document_id: Annotated[int | None, Query(description="Export one document only")] = None,
    ) -> Response:
        records = documents_for_export(session, document_id)
        if document_id is not None and not records:
            raise HTTPException(404, "Document not found")
        name = f"document-{document_id}.xlsx" if document_id else "extractions.xlsx"
        return Response(
            build_workbook(records),
            media_type=XLSX_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def run() -> None:
    import os

    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("PORT", "4102")))


if __name__ == "__main__":
    run()
