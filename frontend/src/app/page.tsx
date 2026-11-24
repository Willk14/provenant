import Link from "next/link";

const AUDIENCES = [
  {
    id: "law",
    who: "Law firms",
    headline: "Review and draft faster, with every point tied to the clause.",
    body: "Give associates and paralegals an AI agent that reads only the contract in front of it and cites the exact words it relied on.",
    gets: [
      "Contract review against your firm's own playbook",
      "Plain-language answers that quote the clause and page",
      "First drafts of notices, amendments and letters, with gaps left visible",
      "A lawyer approves every finding and draft before it counts",
    ],
  },
  {
    id: "agents",
    who: "Tax and property agents",
    headline: "Turn client paperwork into filed-ready working papers.",
    body: "Bring in trial balances, ledgers, statements and leases, and hand clients figures you can defend line by line.",
    gets: [
      "Kenya corporation tax computations with capital allowances and instalments",
      "eTIMS, VAT, PAYE and withholding tax checks against the books",
      "Lease terms such as rent, escalations and options read straight from the PDF",
      "One Excel working-papers pack per client year",
    ],
  },
  {
    id: "finance",
    who: "Finance teams and lenders",
    headline: "Know where every number came from before anyone asks.",
    body: "Close the gap between the contract and the ledger, from lease accounting to the covenant certificate.",
    gets: [
      "IFRS 16 schedules built from verified lease terms and amendments",
      "Leverage, interest cover and liquidity tested each quarter",
      "Cure windows, step-ups and agreement-defined EBITDA handled",
      "A maker-checker trail that holds up in audit",
    ],
  },
];

const PROOF = [
  { title: "Traced, not typed", body: "Every figure opens the page and box, trial balance line or input it came from." },
  { title: "AI that shows its working", body: "Quotes are located on the cited page in code, and any figure must appear in what was cited." },
  { title: "People sign off", body: "Nothing suggested is used until someone confirms it, and nobody approves their own work." },
  { title: "Your clients' data stays separate", body: "Each firm's data is walled off in the database, and history cannot be rewritten." },
];

const PROBLEMS = [
  {
    title: "Hours lost to retyping",
    body: "Rent, rates, thresholds and balances are copied by hand from PDFs and exports into spreadsheets, one figure at a time.",
  },
  {
    title: "Numbers nobody can trace",
    body: "When a partner, client, KRA or an auditor asks where a figure came from, someone has to dig back through the file to find out.",
  },
  {
    title: "AI that sounds sure but shows nothing",
    body: "General chatbots answer about contracts from memory, with no page, no clause and no way to check them before relying on them.",
  },
  {
    title: "Deadlines spread across too many tools",
    body: "Tax computations, lease schedules and covenant certificates live in separate files, so dates and dependencies slip.",
  },
];

const SOLUTION = [
  {
    title: "Read it once",
    body: "Provenant reads leases, facility agreements, trial balances, ledgers and statements, and keeps a link from every figure to its source.",
  },
  {
    title: "Let the agents do the first pass",
    body: "Claude-powered agents review contracts, answer questions and draft letters, citing the exact words they used. Calculations follow Kenya tax rules, IFRS 16 and the agreement's own definitions.",
  },
  {
    title: "Check, sign off and send",
    body: "Reviewers confirm what was read, a different person approves, and the working papers export with a source on every line.",
  },
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
          <a href="#problem">The problem</a>
          <a href="#solution">The solution</a>
          <a href="#law">Who it&apos;s for</a>
          <a href="#trust">Why trust it</a>
        </nav>
        <Link href="/login" className="btn btn-primary">Sign in</Link>
      </header>

      <main>
        <section className="landing-hero">
          <div className="landing-hero-copy">
            <span className="landing-eyebrow">For law firms, agents and finance teams in Kenya</span>
            <h1>Stop retyping contracts into spreadsheets. Start signing off work you can prove.</h1>
            <p>
              Lawyers, agents and finance teams spend hours copying figures out of documents, then more hours proving
              where they came from. Provenant reads the documents, does the first pass with AI agents and Kenya-ready
              calculations, and traces every clause and figure back to its source.
            </p>
            <div className="landing-actions">
              <Link href="/login" className="btn btn-primary btn-lg">Sign in to your firm</Link>
              <a href="#problem" className="btn btn-lg">See the problem we solve</a>
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
            <figcaption className="faint">Click any figure to see the page and the words behind it.</figcaption>
          </figure>
        </section>

        <section id="problem" className="landing-section">
          <span className="landing-kicker">The problem</span>
          <h2>Professional work is still built on copy and paste.</h2>
          <p className="landing-lead">
            The judgement is what clients pay for, yet most of the hours go into moving numbers between documents and
            then defending them.
          </p>
          <div className="landing-grid landing-grid-4">
            {PROBLEMS.map((p) => (
              <article key={p.title} className="landing-card landing-problem">
                <h3>{p.title}</h3>
                <p>{p.body}</p>
              </article>
            ))}
          </div>
        </section>

        <section id="solution" className="landing-section landing-band">
          <span className="landing-kicker">The solution</span>
          <h2>One workspace that reads, calculates and proves.</h2>
          <p className="landing-lead">
            Provenant does the reading and the first draft, and keeps the evidence attached. Your people keep the
            judgement and the sign-off.
          </p>
          <ol className="landing-steps">
            {SOLUTION.map((x) => <li key={x.title}><b>{x.title}.</b> {x.body}</li>)}
          </ol>
        </section>

        <section className="landing-section landing-audiences" aria-label="Who it is for">
          <span className="landing-kicker">Who it is for</span>
          {AUDIENCES.map((a) => (
            <article key={a.id} id={a.id} className="landing-audience">
              <span className="landing-eyebrow">{a.who}</span>
              <h2>{a.headline}</h2>
              <p className="landing-lead">{a.body}</p>
              <ul className="landing-checks">
                {a.gets.map((g) => <li key={g}>{g}</li>)}
              </ul>
            </article>
          ))}
        </section>

        <section id="trust" className="landing-section">
          <h2>Built to be checked, not just trusted</h2>
          <p className="landing-lead">
            Professional work has to survive a partner&apos;s review, a client&apos;s questions and an audit. Provenant is
            designed around that.
          </p>
          <div className="landing-grid landing-grid-4">
            {PROOF.map((t) => (
              <article key={t.title} className="landing-card">
                <h3>{t.title}</h3>
                <p>{t.body}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="landing-cta">
          <h2>Spend your time on judgement, not on retyping.</h2>
          <p className="landing-lead">Bring one contract or one client year, and see every number trace back to its source.</p>
          <Link href="/login" className="btn btn-primary btn-lg">Sign in</Link>
        </section>
      </main>

      <footer className="landing-footer">
        <span>Provenant</span>
        <span>Outputs are working papers for qualified professionals, not tax, legal or investment advice.</span>
      </footer>
    </div>
  );
}
