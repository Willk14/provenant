import Link from "next/link";

const PILLARS = [
  {
    title: "Kenya corporation tax",
    body: "Map a trial balance to the statements and its tax treatment, then compute tax with every adjustment traced to the accounts behind it.",
    points: ["Capital allowances from your register", "Instalments and due dates", "eTIMS, VAT, PAYE and withholding checks"],
  },
  {
    title: "IFRS 16 leases",
    body: "Read lease terms from the PDF, verify each one against its box on the page, and build the schedule and remeasurements from them.",
    points: ["Amendments handled as remeasurements", "Excel schedules for the file", "Review queue for judgement calls"],
  },
  {
    title: "Debt covenants",
    body: "Read the thresholds from the facility agreement and test each quarter's trial balance against them before the certificate is due.",
    points: ["Leverage, interest cover and liquidity", "Cure windows and step-ups", "Agreement-defined EBITDA and caps"],
  },
];

const AGENTS = [
  { title: "Contract reviewer", body: "Reads a contract against your firm's playbook and records findings a lawyer decides." },
  { title: "Contract chat", body: "Answers plain-language questions and quotes the exact wording it relied on." },
  { title: "Paralegal drafter", body: "Prepares notices, amendments and letters, leaving anything unknown as a visible gap." },
];

const TRUST = [
  { title: "Every figure has a source", body: "A box on a PDF page, a trial balance line, a preparer input or a supporting document." },
  { title: "A person decides", body: "Nothing suggested is used until someone confirms it, and nobody reviews their own work." },
  { title: "Checked AI citations", body: "Quotes are located on the cited page in code, and figures must appear in what was cited." },
  { title: "Your firm's data stays yours", body: "Row-level security separates every firm, and history tables cannot be rewritten." },
];

export default function Landing() {
  return (
    <div className="landing">
      <header className="landing-bar">
        <Link href="/" className="brand">
          <span className="brand-mark" aria-hidden>P</span>
          Provenant
        </Link>
        <nav className="landing-links" aria-label="Page sections">
          <a href="#work">What it does</a>
          <a href="#agents">AI agents</a>
          <a href="#trust">Why trust it</a>
        </nav>
        <Link href="/login" className="btn btn-primary">Sign in</Link>
      </header>

      <main>
        <section className="landing-hero">
          <div className="landing-hero-copy">
            <span className="landing-eyebrow">For accounting and law firms in Kenya</span>
            <h1>The work between the contract and the ledger, with every number traced to its source.</h1>
            <p>
              Provenant turns leases, facility agreements and trial balances into tax computations, IFRS 16 schedules and
              covenant tests your reviewers can check line by line.
            </p>
            <div className="landing-actions">
              <Link href="/login" className="btn btn-primary btn-lg">Sign in to your firm</Link>
              <a href="#work" className="btn btn-lg">See what it does</a>
            </div>
          </div>
          <figure className="landing-trace" aria-label="Example of a figure traced to its source">
            <div className="trace-doc">
              <div className="trace-line" />
              <div className="trace-line short" />
              <div className="trace-line" />
              <div className="trace-hit">Monthly Rent: KES 450,000.00</div>
              <div className="trace-line" />
              <div className="trace-line short" />
            </div>
            <div className="trace-card">
              <span className="faint">Monthly payment</span>
              <b className="num">450,000.00</b>
              <span className="trace-source">westlands-office-lease.pdf · page 1</span>
              <span className="pill pill-ok">Verified by a reviewer</span>
            </div>
            <figcaption className="faint">Every figure opens the page and box it came from.</figcaption>
          </figure>
        </section>

        <section id="work" className="landing-section">
          <h2>One workspace for three kinds of work</h2>
          <div className="landing-grid">
            {PILLARS.map((p) => (
              <article key={p.title} className="landing-card">
                <h3>{p.title}</h3>
                <p>{p.body}</p>
                <ul>{p.points.map((x) => <li key={x}>{x}</li>)}</ul>
              </article>
            ))}
          </div>
        </section>

        <section id="agents" className="landing-section landing-band">
          <h2>AI agents that show their working</h2>
          <p className="landing-lead">
            Claude reads only the contract you choose and cites the exact words it used. A qualified person decides what
            the work means.
          </p>
          <div className="landing-grid">
            {AGENTS.map((a) => (
              <article key={a.title} className="landing-card">
                <h3>{a.title}</h3>
                <p>{a.body}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="landing-section">
          <h2>How it works</h2>
          <ol className="landing-steps">
            <li><b>Upload</b> contracts and client records, or email them to your firm&apos;s inbox.</li>
            <li><b>Check</b> what was read: each figure links to its source and waits for a reviewer.</li>
            <li><b>Sign off</b> the computation, schedule or covenant test, and export the working papers.</li>
          </ol>
        </section>

        <section id="trust" className="landing-section">
          <h2>Built to be checked</h2>
          <div className="landing-grid landing-grid-4">
            {TRUST.map((t) => (
              <article key={t.title} className="landing-card">
                <h3>{t.title}</h3>
                <p>{t.body}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="landing-cta">
          <h2>Ready to trace your next file?</h2>
          <Link href="/login" className="btn btn-primary btn-lg">Sign in</Link>
        </section>
      </main>

      <footer className="landing-footer">
        <span>Provenant</span>
        <span>Outputs are working papers for qualified professionals, not tax or legal advice.</span>
      </footer>
    </div>
  );
}
