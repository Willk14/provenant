"""Upload demo documents to a running dev API (scripts/dev.py) as Pat, the preparer.

    python scripts/seed_demo.py [http://127.0.0.1:8000]

The office lease has labelled terms (the baseline extractor reads all of it) and a first amendment that
extends it and raises the rent from its second year. The equipment lease is written in prose, so some terms
must be entered by hand by drawing a box on the PDF, which is what the workspace is for.
"""
import sys

import httpx
from fpdf import FPDF

PAT = {"X-Tenant-Id": "demo-firm", "X-User": "pat@demo-firm", "X-Roles": "preparer"}

OFFICE_LEASE = [
    [
        ("h", "OFFICE LEASE AGREEMENT"),
        ("p", "This Lease is made between Westlands Towers Ltd (the \"Landlord\") and Kilima Analytics Ltd "
              "(the \"Tenant\") for Suite 1204, Westlands Towers, Nairobi."),
        ("s", "1. Key Terms"),
        ("p", "Commencement Date: 1 February 2026"),
        ("p", "Lease Term: 36 months"),
        ("p", "Monthly Rent: KES 450,000.00 payable monthly in advance"),
        ("p", "Discount Rate: 13.5%"),
        ("s", "2. Use"),
        ("p", "The premises shall be used as offices for the Tenant's analytics business and for no other purpose "
              "without the Landlord's prior written consent."),
    ],
    [
        ("s", "3. Service Charge"),
        ("p", "In addition to Rent, the Tenant shall pay a service charge of KES 38,000 per month, adjusted "
              "annually to reflect actual costs. The service charge is a non-lease component."),
        ("s", "4. Renewal"),
        ("p", "The Tenant may renew for a further 24 months by giving six months' written notice. The Tenant is "
              "not reasonably certain to exercise this option at commencement."),
        ("s", "5. Signatures"),
        ("p", "Signed for the Landlord ____________________    Signed for the Tenant ____________________"),
    ],
]

OFFICE_AMENDMENT = [
    [
        ("h", "FIRST AMENDMENT TO OFFICE LEASE"),
        ("p", "This Amendment is made between Westlands Towers Ltd (the \"Landlord\") and Kilima Analytics Ltd "
              "(the \"Tenant\") and amends the Office Lease Agreement for Suite 1204 dated 1 February 2026."),
        ("s", "1. Extension and new rent"),
        ("p", "The parties agree to extend the lease and to revise the rent as follows, with effect from the "
              "Effective Date below. All other terms of the Lease remain unchanged."),
        ("p", "Effective Date: 1 February 2027"),
        ("p", "Lease Term: 48 months"),
        ("p", "Monthly Rent: KES 495,000.00"),
        ("p", "Discount Rate: 14.25%"),
        ("s", "2. Signatures"),
        ("p", "Signed for the Landlord ____________________    Signed for the Tenant ____________________"),
    ]
]

EQUIPMENT_LEASE = [
    [
        ("h", "EQUIPMENT RENTAL AGREEMENT"),
        ("p", "Kilima Analytics Ltd (the Lessee) agrees to rent two data-centre storage arrays from "
              "Savanna Leasing Ltd (the Lessor) on the terms below."),
        ("p", "Commencement Date: 1 March 2026"),
        ("p", "Lease Term: 24 months"),
        ("p", "The Lessee shall pay KES 120,000 each month, in arrears, by bank transfer."),
        ("p", "For IFRS 16 purposes the Lessee's incremental borrowing rate is 14% per annum."),
        ("p", "Late payments accrue interest at 2% per month."),
    ]
]


def make_pdf(pages) -> bytes:
    pdf = FPDF()
    pdf.set_margins(22, 22, 22)
    for blocks in pages:
        pdf.add_page()
        for kind, text in blocks:
            size, style, gap = {"h": (16, "B", 6), "s": (12, "B", 3), "p": (11, "", 3)}[kind]
            pdf.set_font("Helvetica", style, size)
            pdf.multi_cell(0, 6.5, text, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(gap)
    return bytes(pdf.output())


def upload(base: str, name: str, pages, amends: str | None = None) -> str:
    r = httpx.post(
        f"{base}/documents",
        headers=PAT,
        data={"amends": amends} if amends else None,
        files={"file": (name, make_pdf(pages), "application/pdf")},
    )
    r.raise_for_status()
    print(f"{r.status_code} {name} -> {r.json()['id']}")
    return r.json()["id"]


def main(base: str) -> None:
    office = upload(base, "westlands-office-lease.pdf", OFFICE_LEASE)
    upload(base, "westlands-first-amendment.pdf", OFFICE_AMENDMENT, amends=office)
    upload(base, "storage-array-rental.pdf", EQUIPMENT_LEASE)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000")
