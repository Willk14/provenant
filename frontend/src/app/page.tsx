import Link from "next/link";

const FIRMS = [
  {
    id: "larger",
    who: "Larger firms",
    headline: "Consistency across every team, however complex the work.",
    body: "As matters grow and clients expect more, the same playbook, precedents and checks need to hold in every department and for every associate.",
    gets: [
      "Contracts reviewed against the firm's own playbook, the same way every time",
      "Drafts filled from locked precedents, with any departure shown to a partner",
      "Conflict checks, KYC and deadlines in one place for every matter",
      "Maker-checker sign-off and a history nobody can rewrite",
    ],
  },
  {
    id: "independent",
    who: "Independent practices",
    headline: "Practical tools that let a small team move like a large one.",
    body: "Do the first pass in minutes rather than hours, and compete on speed and quality without adding staff.",
    gets: [
      "Ask a contract a question and get the answer with the clause and page",
      "First drafts of notices, amendments and letters, with gaps left visible",
      "Court notices turned into deadlines, with weekends and court holidays counted",
      "One list each morning of what is waiting for you",
    ],
  },
];

const PILLARS = [
  {
    title: "Built for Kenyan law",
    body: "Deadlines follow the rules and court holidays your firm sets, conflict and KYC checks follow Kenyan practice, and tax work follows the Income Tax Act.",
  },
  {
    title: "Built for legal professionals",
    body: "Matters, precedents, the docket, the client account and due diligence, organised the way advocates already work, with a qualified person deciding every step.",
  },
  {
    title: "Powered by legal AI",
    body: "Claude reads, drafts and checks, and every answer is tied to the exact words it relied on. Anything it cannot show is never presented as fact.",
  },
];

const WORK = [
  { title: "Analyse", body: "Review contracts against your playbook and see every finding on the page it came from." },
  { title: "Draft", body: "Fill the firm's precedents and prepare letters and notices, with gaps left for a lawyer to complete." },
  { title: "Manage", body: "Matters, deadlines, conflicts, KYC and the client account, with everything waiting for you in one list." },
];

const PROOF = [
  { title: "Traced, not typed", body: "Every clause and figure opens the page, box or line it came from." },
  { title: "AI that shows its working", body: "Quotes are found on the cited page in code, and any figure must appear in what was cited." },
  { title: "People sign off", body: "Nothing suggested is used until someone confirms it, and nobody approves their own work." },
  { title: "Your clients' data stays separate", body: "Each firm's data is walled off in the database, and history cannot be rewritten." },
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
          <a href="#firms">For your firm</a>
          <a href="#pillars">Why Provenant</a>
          <a href="#work">What it does</a>
          <a href="#trust">Why trust it</a>
        </nav>
        <Link href="/login" className="btn btn-primary">Sign in</Link>
      </header>

      <main>
        <section className="landing-hero landing-hero-solo">
          <div className="landing-hero-copy">
            <span className="landing-eyebrow">Legal AI and workflow for Kenyan law firms</span>
            <h1>Faster legal work, with every answer tied to its source.</h1>
            <p>
              Larger firms are handling more complex matters, higher client expectations and the need for consistency
              across teams. Independent practices need practical tools that help them move faster and compete with
              confidence. Provenant brings legal AI and workflow together in one workspace, built for Kenyan law and
              the way Kenyan advocates work.
            </p>
            <div className="landing-actions">
              <Link href="/login" className="btn btn-primary btn-lg">Sign in to your firm</Link>
              <a href="#firms" className="btn btn-lg">See how it helps your firm</a>
            </div>
          </div>
        </section>

        <section id="firms" className="landing-section landing-audiences" aria-label="For your firm">
          <span className="landing-kicker">Whatever the size of your firm</span>
          <div className="landing-grid landing-grid-2">
            {FIRMS.map((f) => (
              <article key={f.id} id={f.id} className="landing-card">
                <span className="landing-eyebrow">{f.who}</span>
                <h3>{f.headline}</h3>
                <p>{f.body}</p>
                <ul>{f.gets.map((g) => <li key={g}>{g}</li>)}</ul>
              </article>
            ))}
          </div>
        </section>

        <section id="pillars" className="landing-section landing-band">
          <span className="landing-kicker">Why Provenant</span>
          <h2>Made for Kenyan legal practice, not adapted to it.</h2>
          <div className="landing-grid">
            {PILLARS.map((p) => (
              <article key={p.title} className="landing-card landing-pillar">
                <h3>{p.title}</h3>
                <p>{p.body}</p>
              </article>
            ))}
          </div>
        </section>

        <section id="work" className="landing-section">
          <span className="landing-kicker">What it does</span>
          <h2>Analyse, draft and manage legal work with speed, structure and confidence.</h2>
          <ol className="landing-steps">
            {WORK.map((x) => <li key={x.title}><b>{x.title}.</b> {x.body}</li>)}
          </ol>
        </section>

        <section id="trust" className="landing-section landing-band">
          <h2>Built to be checked, not just trusted</h2>
          <p className="landing-lead">
            Legal work has to survive a partner&apos;s review, a client&apos;s questions and the court. Provenant is
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
          <h2>Spend your time on judgement, not on paperwork.</h2>
          <p className="landing-lead">Bring one contract or one matter, and see every answer trace back to its source.</p>
          <Link href="/login" className="btn btn-primary btn-lg">Sign in</Link>
        </section>
      </main>

      <footer className="landing-footer">
        <span>Provenant</span>
        <span>Outputs are working papers for qualified professionals, not legal, tax or investment advice.</span>
      </footer>
    </div>
  );
}
