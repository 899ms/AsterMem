/**
 * Public product landing page. Accessible without login, explains AsterMem's positioning,
 * capabilities, and workflow. The main button enters the protected app home page,
 * with AuthGate handling login redirects.
 */
import { Link } from "react-router-dom";
import { IconArrowRight, IconBrandGithub } from "@tabler/icons-react";
import { LocaleSwitcher, useI18n } from "../i18n";
import { LICENSE_NAME, LICENSE_URL, SOURCE_URL } from "../license";

export function LandingPage() {
  const { t } = useI18n();

  const features = [
    ["01", "Get the information in", "Write in Markdown, add tags and priorities, and let AsterMem split long content into semantic chunks."],
    ["02", "If you cannot find it, storing it was pointless", "Keyword and vector search run together. Change the wording and AsterMem can still pull the right content back."],
    ["03", "Give AI the context", "Ask your memory library directly. AI finds related content, digs deeper, and saves useful conclusions back."],
    ["04", "Put the connections on the screen", "Use the knowledge graph, timeline, and vector space to see how your memories connect."],
    ["05", "Stop moving things one by one", "Import text, files, and bookmarks directly. Let AI organize them, then export a backup whenever you need one."],
    ["06", "Choose your own models", "Connect local LM Studio or cloud providers. Configure embedding and chat models separately."],
  ];

  return (
    <div className="landing-page">
      <header className="landing-nav">
        <Link to="/" className="landing-logo" aria-label="AsterMem">
          <img src="/astermem-icon.png" alt="" aria-hidden="true" />
          <span>ASTERMEM</span>
        </Link>
        <nav aria-label={t("Landing navigation")}>
          <a href="#features">{t("Features")}</a>
          <a href="#how-it-works">{t("How it works")}</a>
          <Link to="/methodology">{t("Methodology")}</Link>
          <a
            className="landing-nav-icon"
            href={SOURCE_URL}
            target="_blank"
            rel="noreferrer"
            aria-label={t("Source code")}
            title={t("Source code")}
          >
            <IconBrandGithub aria-hidden="true" />
          </a>
          <LocaleSwitcher />
          <Link to="/home" className="landing-nav-cta">
            {t("Open AsterMem")}
          </Link>
        </nav>
      </header>

      <main>
        <section className="landing-hero">
          <div className="landing-hero-copy">
            <span className="kicker">{t("Self-hosted · AI-ready · Private")}</span>
            <h1>{t("Turn scattered information into memory you can recall.")}</h1>
            <p>{t("AsterMem runs on your own machine. Bring in notes, files, and conversations. Search them, ask AI, or map the connections. You control the data and the models.")}</p>
            <div className="landing-actions">
              <Link to="/home" className="btn primary">
                {t("Get started")}
                <IconArrowRight aria-hidden="true" />
              </Link>
              <a href="#features" className="btn">
                {t("Explore features")}
              </a>
              <a href={SOURCE_URL} className="btn" target="_blank" rel="noreferrer">
                <IconBrandGithub aria-hidden="true" />
                GitHub
              </a>
            </div>
          </div>
          <div className="landing-hero-visual" aria-label={t("AsterMem workflow preview")}>
            <div className="landing-memory-card">
              <span>MEMORY / 024</span>
              <strong>{t("Give your AI long-term memory")}</strong>
              <p>{t("Chunk by meaning, run hybrid search, and build a knowledge graph. Your information stops collecting dust.")}</p>
              <div>
                <em># KNOWLEDGE</em>
                <em># PRIVATE</em>
              </div>
            </div>
            <div className="landing-search-line">
              <span>HYBRID SEARCH</span>
              <b>0.94</b>
            </div>
            <div className="landing-node node-one">A</div>
            <div className="landing-node node-two">B</div>
            <div className="landing-node node-three">C</div>
          </div>
        </section>

        <section className="landing-manifesto">
          <span className="kicker">{t("Why AsterMem")}</span>
          <div className="landing-manifesto-copy">
            <p>{t("Notes capture it.")}</p>
            <p>{t("AsterMem brings it back. AI puts it to work.")}</p>
            <p>{t("No more digging through folders and hoping.")}</p>
          </div>
        </section>

        <section className="landing-methodology">
          <div className="landing-methodology-meta">
            <span className="kicker">ASTERMEM / {t("Methodology").toUpperCase()}</span>
            <span>08 {t("Principles").toUpperCase()}</span>
          </div>
          <div className="landing-methodology-grid">
            <div>
              <h2>{t("A memory system needs a point of view.")}</h2>
              <Link to="/methodology" className="btn">
                {t("Read the methodology")}
                <IconArrowRight aria-hidden="true" />
              </Link>
            </div>
            <div className="landing-methodology-copy">
              <p>{t("Original material stays authoritative. Retrieval should guide the next step. Every AI conclusion must be traceable.")}</p>
              <ol>
                <li><em>01</em><span>{t("Original text, not paraphrases of paraphrases")}</span></li>
                <li><em>02</em><span>{t("Navigation, not one-shot search")}</span></li>
                <li><em>03</em><span>{t("Visible, editable, switchable AI")}</span></li>
              </ol>
            </div>
          </div>
        </section>

        <section id="features" className="landing-section">
          <div className="landing-section-head">
            <span className="kicker">01 / {t("Features")}</span>
            <h2>{t("Capture it. Connect it. Use it.")}</h2>
          </div>
          <div className="landing-feature-grid">
            {features.map(([index, title, description]) => (
              <article key={index}>
                <span>{index}</span>
                <h3>{t(title)}</h3>
                <p>{t(description)}</p>
              </article>
            ))}
          </div>
        </section>

        <section id="how-it-works" className="landing-section landing-flow">
          <div className="landing-section-head">
            <span className="kicker">02 / {t("How it works")}</span>
            <h2>{t("Three steps from material to usable memory")}</h2>
          </div>
          <div className="landing-flow-grid">
            <article>
              <span>01</span>
              <h3>{t("Bring it in")}</h3>
              <p>{t("Write directly or import the material you already have.")}</p>
            </article>
            <article>
              <span>02</span>
              <h3>{t("Process it")}</h3>
              <p>{t("AsterMem chunks the content and creates tags and vector indexes.")}</p>
            </article>
            <article>
              <span>03</span>
              <h3>{t("Pull it back")}</h3>
              <p>{t("Use search, AI exploration, or graphs. Pull back the exact context you need.")}</p>
            </article>
          </div>
        </section>

        <section className="landing-privacy">
          <div>
            <span className="kicker">{t("Private by design")}</span>
            <h2>{t("Keep your memories on your machine")}</h2>
          </div>
          <p>{t("You manage the database, files, and account. You can turn sign-in protection on or off. Connect a local model if you want. You decide where the data goes.")}</p>
        </section>

        <section className="landing-final-cta">
          <span className="kicker">ASTERMEM / ASTEROVE</span>
          <h2>{t("Bring your scattered material back. Start now.")}</h2>
          <div className="landing-actions">
            <Link to="/home" className="btn primary">
              {t("Open AsterMem")}
              <IconArrowRight aria-hidden="true" />
            </Link>
            <a href={SOURCE_URL} className="btn" target="_blank" rel="noreferrer">
              <IconBrandGithub aria-hidden="true" />
              GitHub
            </a>
          </div>
        </section>
      </main>

      <footer className="landing-footer">
        <span>ASTERMEM</span>
        <span className="landing-footer-license">
          {t("Free software under")}{" "}
          <a href={LICENSE_URL} target="_blank" rel="noreferrer">{LICENSE_NAME}</a>
          {" · "}
          <a href={SOURCE_URL} target="_blank" rel="noreferrer">{t("Source code")}</a>
        </span>
        <span>© 2026 ASTEROVE</span>
      </footer>
    </div>
  );
}
