"""Upload two demo leases to a running dev API (scripts/dev.py) as Pat, the preparer.

    python scripts/seed_demo.py [http://127.0.0.1:8000]

One lease has labelled terms (the baseline extractor reads all of it); the other is written in prose, so some
terms must be entered by hand by drawing a box on the PDF, which is what the workspace is for.
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


def main(base: str) -> None:
    for name, pages in (("westlands-office-lease.pdf", OFFICE_LEASE), ("storage-array-rental.pdf", EQUIPMENT_LEASE)):
        r = httpx.post(f"{base}/documents", headers=PAT, files={"file": (name, make_pdf(pages), "application/pdf")})
        r.raise_for_status()
        print(f"{r.status_code} {name} -> {r.json()['id']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000")
