"""Upload demo documents to a running dev API (scripts/dev.py) as Pat, the preparer.

    python scripts/seed_demo.py [http://127.0.0.1:8000]

The office lease has labelled terms (the baseline extractor reads all of it) and a first amendment that
extends it and raises the rent from its second year. The equipment lease is written in prose, so some terms
must be entered by hand by drawing a box on the PDF, which is what the workspace is for.
The term facility agreement has three financial covenants; five quarterly trial balances show a borrower
passing comfortably, then breaching after a weak quarter.
"""
import csv
import io
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
        ("p", "Rent escalation: from month 13 the monthly rent is KES 472,500.00 and from month 25 it is "
              "KES 496,125.00. The first two months of the Lease Term are rent free."),
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

FACILITY_AGREEMENT = [
    [
        ("h", "TERM FACILITY AGREEMENT"),
        ("p", "KES 1,000,000,000 term loan facility between Kilima Analytics Ltd (the \"Borrower\") and "
              "Rift Valley Bank plc (the \"Lender\")."),
        ("s", "21. Information undertakings"),
        ("p", "The Borrower shall supply a Compliance Certificate to the Lender within 45 days after the end of "
              "each Financial Quarter, setting out computations of the financial covenants in Clause 22."),
        ("s", "22. Financial covenants"),
        ("p", "(a) Leverage: The Leverage Ratio in respect of any Relevant Period shall not exceed 3.50:1."),
        ("p", "(b) Interest cover: Interest Cover in respect of any Relevant Period shall not be less than 4.00:1."),
        ("p", "(c) Minimum liquidity: Liquidity shall not at any time be less than KES 50,000,000."),
        ("p", "First Test Date: 30 June 2026"),
    ],
    [
        ("s", "23. Equity cure"),
        ("p", "If a financial covenant is not met, the Borrower may remedy that breach by procuring new equity: "
              "the equity cure must be received within 20 days of the due date of the relevant Compliance "
              "Certificate."),
        ("s", "24. Interest"),
        ("p", "Interest rate: 13.5% per annum, payable quarterly in arrears."),
    ],
]

# Kilima's quarterly trial balances (KES millions): steady for four quarters, then a weak quarter.
STEADY_QUARTER = dict(revenue=300, opex=225, da=30, interest=18, cash=120, borrowings=900, leases=60)
WEAK_QUARTER = dict(revenue=260, opex=235, da=30, interest=20, cash=60, borrowings=950, leases=58)
TB_QUARTERS = [
    ("2025-09-30", STEADY_QUARTER),
    ("2025-12-31", STEADY_QUARTER),
    ("2026-03-31", STEADY_QUARTER),
    ("2026-06-30", STEADY_QUARTER),
    ("2026-09-30", WEAK_QUARTER),
]


def trial_balance_csv(revenue, opex, da, interest, cash, borrowings, leases) -> bytes:
    m = 1_000_000
    lines = [
        ("1000", "Bank - Rift Valley current account", "cash", cash * m),
        ("1100", "Trade receivables", "other", 410 * m),
        ("1500", "Property, plant and equipment", "other", 1_250 * m),
        ("1600", "Right-of-use assets", "other", 55 * m),
        ("2000", "Trade payables", "other", -180 * m),
        ("2500", "Term loan - Rift Valley Bank", "borrowings", -borrowings * m),
        ("2600", "Lease liabilities", "lease_liabilities", -leases * m),
        ("4000", "Revenue - analytics subscriptions", "revenue", -revenue * m),
        ("5000", "Staff costs", "operating_expenses", round(opex * 0.6) * m),
        ("5100", "Cloud and hosting", "operating_expenses", (opex - round(opex * 0.6)) * m),
        ("6000", "Depreciation and amortisation", "depreciation_amortisation", da * m),
        ("6100", "Interest on term loan", "interest_expense", interest * m),
    ]
    lines.append(("3000", "Share capital and retained earnings", "other", -sum(b for *_, b in lines)))
    out = io.StringIO()
    writer = csv.writer(out)  # quotes names like "Property, plant and equipment"
    writer.writerow(["code", "name", "category", "balance"])
    writer.writerows([c, n, k, f"{b:.2f}"] for c, n, k, b in lines)
    return out.getvalue().encode()


# Kilima's statutory trial balance for the year ended 31 December 2026 (KES), for the tax computation.
CLIENT_TB = [
    # Includes a KES 302.88m year-end cash reserve so the opening equity position is explicit below.
    ("1000", "Equity Bank current account", 341_330_000),
    ("1010", "M-Pesa till float", 1_250_000),
    ("1100", "Trade receivables", 96_300_000),
    ("1500", "Computers and office equipment - cost", 64_000_000),
    ("1510", "Accumulated depreciation - office equipment", -21_500_000),
    ("1600", "Right-of-use asset - Westlands office", 10_400_000),
    ("2000", "Trade payables", -18_700_000),
    ("2100", "Accruals", -6_200_000),
    ("2500", "Term loan - Rift Valley Bank", -95_000_000),
    ("2600", "Lease liabilities", -10_250_000),
    ("3000", "Share capital", -20_000_000),
    ("4000", "Subscription revenue", -1_160_000_000),
    ("4100", "Dividend income - unit trust", -2_400_000),
    ("4200", "Interest income - call deposits", -3_100_000),
    ("5000", "Cost of sales - cloud hosting", 384_000_000),
    ("5100", "Staff salaries and wages", 431_000_000),
    ("5110", "NSSF and SHIF contributions", 18_600_000),
    ("5200", "Office and administrative expenses", 52_400_000),
    ("5210", "Marketing and events", 21_300_000),
    ("5220", "Donations - Kibera school", 1_500_000),
    ("5230", "KRA penalties and interest", 850_000),
    ("5240", "Client entertainment", 2_100_000),
    ("5250", "Sundry 7", 640_000),
    ("5400", "Depreciation - office equipment", 12_800_000),
    ("5410", "Depreciation - right-of-use asset", 4_950_000),
    ("5500", "Interest on term loan", 12_400_000),
    ("5510", "Interest on lease liabilities", 1_330_000),
]
# Debit-positive convention: KES 120m opening retained earnings is a credit balance.
# FY2026 profit is KES 221.63m, so closing retained earnings is a credit KES 341.63m.
CLIENT_TB.append(("3100", "Retained earnings brought forward", -120_000_000))
if sum(balance for _, _, balance in CLIENT_TB) != 0:
    raise ValueError("Kilima's FY2026 statutory trial balance must balance to zero")
if next(balance for code, _, balance in CLIENT_TB if code == "3100") >= 0:
    raise ValueError("Kilima's retained earnings brought forward must be a credit balance")


def client_tb_csv() -> bytes:
    out = io.StringIO()
    csv.writer(out).writerows([["code", "name", "balance"], *([c, n, f"{b:.2f}"] for c, n, b in CLIENT_TB)])
    return out.getvalue().encode()


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


def upload(base: str, name: str, pages, amends: str | None = None, kind: str = "lease") -> str:
    r = httpx.post(
        f"{base}/documents",
        headers=PAT,
        data={"amends": amends} if amends else {"kind": kind},
        files={"file": (name, make_pdf(pages), "application/pdf")},
    )
    r.raise_for_status()
    print(f"{r.status_code} {name} -> {r.json()['id']}")
    return r.json()["id"]


def main(base: str) -> None:
    office = upload(base, "westlands-office-lease.pdf", OFFICE_LEASE)
    upload(base, "westlands-first-amendment.pdf", OFFICE_AMENDMENT, amends=office)
    upload(base, "storage-array-rental.pdf", EQUIPMENT_LEASE)
    facility = upload(base, "rift-valley-term-facility.pdf", FACILITY_AGREEMENT, kind="credit_agreement")
    for period_end, quarter in TB_QUARTERS:
        r = httpx.post(
            f"{base}/documents/{facility}/trial-balances",
            headers=PAT,
            data={"period_end": period_end},
            files={"file": (f"kilima-tb-{period_end}.csv", trial_balance_csv(**quarter), "text/csv")},
        )
        r.raise_for_status()
        print(f"{r.status_code} trial balance {period_end}")

    client = httpx.post(f"{base}/clients", headers=PAT, json={"name": "Kilima Analytics Ltd"})
    client.raise_for_status()
    cid = client.json()["id"]
    r = httpx.post(f"{base}/clients/{cid}/trial-balances", headers=PAT, data={"year_end": "2026-12-31"},
                   files={"file": ("kilima-tb-fy2026.csv", client_tb_csv(), "text/csv")})
    r.raise_for_status()
    print(f"{r.status_code} client Kilima Analytics Ltd, trial balance FY2026 -> {cid}")
    r = httpx.post(f"{base}/clients/{cid}/years/2026-12-31/confirm-suggestions", headers=PAT)
    r.raise_for_status()
    print(f"{r.status_code} confirmed {r.json()['confirmed']} suggested mappings (Sundry 7 is left for a person)")
    seed_tax_records(base, cid)


FIXED_ASSET_REGISTER = b"""asset_code,description,class,acquired,cost,disposed,proceeds
IT-2019-01,Server room fit-out,Computers and office equipment,2019-04-01,18000000,,
IT-2021-04,Developer workstations (40),Computers and office equipment,2021-02-15,14500000,,
IT-2022-07,Office furniture - Westlands,Computers and office equipment,2022-07-01,9500000,,
IT-2023-02,Network switches and Wi-Fi,Computers and office equipment,2023-03-10,6000000,,
IT-2024-11,Laptops (35),Computers and office equipment,2024-11-20,8000000,,
IT-2022-03,Old laptops (sold to staff),Computers and office equipment,2022-03-01,2500000,2026-09-30,300000
IT-2026-01,GPU servers,Computers and office equipment,2026-02-12,5200000,,
IT-2026-02,Laptops (12),Computers and office equipment,2026-06-03,2800000,,
"""

TAX_INPUTS = {
    "losses_brought_forward": "40000000",
    "prior_return": {"reference": "KRA202507654321", "filed_on": "2026-06-24"},
    "allowances": [{
        "name": "Computers and office equipment", "wdv_brought_forward": "30000000", "additions": "0",
        "rate_percent": "25", "method": "reducing",
        "basis": "Second Schedule, as applied by the preparer for FY2026 (confirm against the Finance Act in force)",
    }],
}

RECEIPT = [[("h", "Kenya Revenue Authority - Payment receipt"), ("p", "PRN 2026-INST-04  Instalment tax, 4th instalment"),
            ("p", "Taxpayer: Kilima Analytics Ltd   Amount: KES 13,500,000.00   Paid: 20 December 2026")]]
WHT_CERT = [[("h", "Withholding tax certificate"), ("p", "Payer: Rift Valley Bank Ltd   Certificate no. WHT-RVB-2026-118"),
             ("p", "Payee: Kilima Analytics Ltd   Interest on call deposits: KES 3,100,000.00"),
             ("p", "Tax withheld at 15%: KES 465,000.00")]]


BANK_CSV = "date,description,amount\n2026-12-20,KRA instalment tax,-13500000\n".encode()


def seed_tax_records(base: str, cid: str, inputs: dict | None = None) -> None:
    """The records a consultant gathers for the year: register, receipts and certificates, credits."""
    year = f"{base}/clients/{cid}/years/2026-12-31"
    r = httpx.post(f"{year}/fixed-assets", headers=PAT,
                   files={"file": ("kilima-fixed-asset-register.csv", FIXED_ASSET_REGISTER, "text/csv")})
    r.raise_for_status()
    ids = {}
    for name, category, code, pages in (
        ("kra-instalment-receipt-q4.pdf", "instalment_receipt", "", RECEIPT),
        ("wht-certificate-rift-valley-bank.pdf", "withholding_certificate", "4200", WHT_CERT),
    ):
        r = httpx.post(f"{year}/attachments", headers=PAT, data={"category": category, "account_code": code},
                       files={"file": (name, make_pdf(pages), "application/pdf")})
        r.raise_for_status()
        ids[category] = r.json()["id"]
    r = httpx.post(f"{year}/attachments", headers=PAT, data={"category": "bank_statement", "note": "December 2026"},
                   files={"file": ("equity-bank-dec-2026.csv", BANK_CSV, "text/csv")})
    r.raise_for_status()
    credits = [
        {"kind": "instalment", "description": f"Instalment {n} of 4", "amount": "13500000",
         "reference": f"PRN 2026-INST-0{n}", "evidence": ids["instalment_receipt"] if n == 4 else None}
        for n in range(1, 5)
    ] + [{"kind": "withholding", "description": "Rift Valley Bank - interest on call deposits", "amount": "465000",
          "reference": "WHT-RVB-2026-118", "evidence": ids["withholding_certificate"]}]
    r = httpx.put(f"{year}/inputs", headers=PAT, json={**(inputs or TAX_INPUTS), "credits": credits})
    r.raise_for_status()
    print(f"{r.status_code} tax records: fixed asset register, 3 supporting documents, 5 credits")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000")
