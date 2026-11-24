"""Due diligence: the contracts in a matter's data room, reviewed in bulk and shown as one matrix (ADR 0019)."""
import io
from dataclasses import asdict

from fastapi import FastAPI, Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from provenant.api import legal
from provenant.api.auth import PARTNER, PREPARER, REVIEWER, Principal
from provenant.api.db import Document, MatterDocumentRow
from provenant.api.matters import _event, get_matter
from provenant.api.services import Conflict, Invalid, NotFound, require
from provenant.practice import diligence

ANY = (PREPARER, REVIEWER, PARTNER)
MAX_DOCUMENTS = 500


class LinkIn(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=MAX_DOCUMENTS)


def _linked(s: Session, tenant_id: str, matter_id: str) -> list[Document]:
    return list(s.scalars(select(Document).join(MatterDocumentRow, MatterDocumentRow.document_id == Document.id)
                          .where(MatterDocumentRow.tenant_id == tenant_id, MatterDocumentRow.matter_id == matter_id)
                          .order_by(Document.filename)))


def matrix(s: Session, p: Principal, matter_id: str) -> dict:
    matter = get_matter(s, p.tenant_id, matter_id)
    topics, _ = legal.playbook(s, p.tenant_id)
    documents = _linked(s, p.tenant_id, matter.id)
    reviews = {}
    for d in documents:
        latest = legal.list_reviews(s, p.tenant_id, d.id)
        if latest:
            r = latest[0]
            reviews[d.id] = {"status": r.status, "findings": [
                {"topic": f.topic, "severity": f.severity, "title": f.title, "quote": f.quote, "page": f.page}
                for f in legal.findings(s, p.tenant_id, r.id)]}
    rows = diligence.build([t.key for t in topics], [(d.id, d.filename) for d in documents], reviews)
    return {"matter": matter.reference, "topics": [{"key": t.key, "label": t.label} for t in topics],
            "rows": [{"document_id": r.document_id, "filename": r.filename, "deviations": r.deviations,
                      "cells": {k: {**asdict(c), "deviation": c.deviation} for k, c in r.cells.items()}} for r in rows],
            "documents": len(documents), "reviewed": sum(1 for d in documents if reviews.get(d.id, {}).get("status") == "done")}


def add_routes(app: FastAPI, Auth, Db) -> None:  # noqa: N803 - FastAPI dependency aliases
    @app.post("/matters/{matter_id}/documents", status_code=201)
    def link(matter_id: str, body: LinkIn, p: Auth, s: Db) -> dict:
        require(p, *ANY)
        matter = get_matter(s, p.tenant_id, matter_id)
        have = {d.id for d in _linked(s, p.tenant_id, matter.id)}
        added = 0
        for document_id in dict.fromkeys(body.document_ids):
            doc = s.scalar(select(Document).where(Document.tenant_id == p.tenant_id, Document.id == document_id))
            if doc is None:
                raise NotFound(f"document {document_id} not found")
            if doc.kind == "amendment":
                raise Invalid(f"{doc.filename} is an amendment; link its contract, which is reviewed with it")
            if doc.id not in have:
                s.add(MatterDocumentRow(tenant_id=p.tenant_id, matter_id=matter.id, document_id=doc.id, added_by=p.user))
                added += 1
        if added:
            _event(s, matter, "documents_linked", p.user, f"{added} contract{'s' if added != 1 else ''}")
        s.commit()
        return {"added": added}

    @app.get("/matters/{matter_id}/documents")
    def linked(matter_id: str, p: Auth, s: Db) -> list[dict]:
        matter = get_matter(s, p.tenant_id, matter_id)
        return [{"id": d.id, "filename": d.filename, "kind": d.kind, "status": d.status}
                for d in _linked(s, p.tenant_id, matter.id)]

    @app.post("/matters/{matter_id}/due-diligence/review")
    def review_all(matter_id: str, p: Auth, s: Db) -> dict:
        """Queue a review for every linked contract that has none yet. Each review is billed like any other."""
        require(p, *ANY)
        matter = get_matter(s, p.tenant_id, matter_id)
        started, skipped = 0, []
        for d in _linked(s, p.tenant_id, matter.id):
            if legal.list_reviews(s, p.tenant_id, d.id):
                continue
            try:
                legal.start_review(s, p, d.id)
                started += 1
            except Conflict as exc:
                skipped.append({"filename": d.filename, "reason": str(exc)})
        _event(s, matter, "diligence_reviews", p.user, f"{started} review(s) queued")
        s.commit()
        return {"started": started, "skipped": skipped}

    @app.get("/matters/{matter_id}/due-diligence")
    def get_matrix(matter_id: str, p: Auth, s: Db) -> dict:
        return matrix(s, p, matter_id)

    @app.get("/matters/{matter_id}/due-diligence.xlsx")
    def export(matter_id: str, p: Auth, s: Db) -> Response:
        data = matrix(s, p, matter_id)
        book = Workbook()
        sheet = book.active
        sheet.title = "Due diligence"
        sheet.append([f"Due diligence matrix: {data['matter']}. Cells come from cited review findings; a lawyer "
                      "decides what they mean. Not found means the review found no clause, not that there is no risk."])
        sheet.append(["Contract", "Deviations", *[t["label"] for t in data["topics"]]])
        for cell in sheet[2]:
            cell.font = Font(bold=True)
        red = PatternFill("solid", fgColor="F8D7DA")
        for row in data["rows"]:
            values = []
            for t in data["topics"]:
                c = row["cells"][t["key"]]
                values.append(f"{c['severity'].upper()}: {c['title']} (p. {c['page']}): \"{c['quote']}\""
                              if c["state"] == "finding" else c["state"].replace("_", " "))
            sheet.append([row["filename"], row["deviations"], *values])
            for i, t in enumerate(data["topics"], start=3):
                if row["cells"][t["key"]]["deviation"]:
                    sheet.cell(sheet.max_row, i).fill = red
        for column in sheet.iter_cols(min_row=2):
            for cell in column:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            sheet.column_dimensions[column[0].column_letter].width = 42
        out = io.BytesIO()
        book.save(out)
        return Response(out.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="due-diligence-{data["matter"]}.xlsx"'})
