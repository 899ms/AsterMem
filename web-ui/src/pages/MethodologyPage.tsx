/**
 * Background: Users need a "methodology introduction" page for visitors/new users explaining
 * AsterMem's design philosophy (source anchoring, dual-layer retrieval, traceable profile, Dream, etc.).
 * Design intent: Content is not hardcoded—read from backend docs/methodology/{lang}.md based on current
 * language, so updating the md file updates the page without rebuilding.
 * Key constraint: Switching language auto-refetches the corresponding version; missing languages fall back to English.
 * Public access: This page requires no login—when unauthenticated, uses a standalone lightweight navbar
 * (same as LandingPage); when authenticated, uses the full Layout sidebar for navigation continuity.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { IconArrowDown, IconArrowRight } from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { Markdown } from "../components/Markdown";
import { EmptyState, LoadingLine } from "../components/EmptyState";
import { api, reportError } from "../api";
import { LocaleSwitcher, useI18n } from "../i18n";

export function MethodologyPage() {
  const { t, locale } = useI18n();
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [activeSection, setActiveSection] = useState(0);
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const articleRef = useRef<HTMLElement>(null);

  useEffect(() => {
    let cancelled = false;
    api<{ authenticated?: boolean }>("GET", "/api/auth/check", undefined, { skipAuthRedirect: true })
      .then((res) => { if (!cancelled) setAuthenticated(res?.authenticated ?? false); })
      .catch(() => { if (!cancelled) setAuthenticated(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api<{ content: string }>("GET", `/api/methodology?lang=${encodeURIComponent(locale)}`, undefined, { skipAuthRedirect: true })
      .then((res) => { if (!cancelled) setContent(res?.content ?? ""); })
      .catch((err) => reportError(err, t("Unable to load the methodology document")))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [locale, t]);

  const document = useMemo(() => {
    const cleanSectionTitle = (value: string) =>
      value.replace(/^(?:[一二三四五六七八九十]+、|\d+[、.])\s*/, "");
    const lines = content.split(/\r?\n/);
    const titleIndex = lines.findIndex((line) => /^#\s+/.test(line));
    const firstSectionIndex = lines.findIndex((line) => /^##\s+/.test(line));
    const title = titleIndex >= 0
      ? lines[titleIndex].replace(/^#\s+/, "").trim()
      : t("How AsterMem works");
    const introStart = titleIndex >= 0 ? titleIndex + 1 : 0;
    const introEnd = firstSectionIndex >= 0 ? firstSectionIndex : lines.length;
    const intro = lines.slice(introStart, introEnd).join("\n").trim();
    const body = firstSectionIndex >= 0
      ? lines
        .slice(firstSectionIndex)
        .map((line) => /^##\s+/.test(line)
          ? `## ${cleanSectionTitle(line.replace(/^##\s+/, "").trim())}`
          : line)
        .join("\n")
        .trim()
      : "";
    const sections = lines
      .filter((line) => /^##\s+/.test(line))
      .map((line) => cleanSectionTitle(line.replace(/^##\s+/, "").trim()));
    return { title, intro, body, sections };
  }, [content, t]);

  useEffect(() => {
    const article = articleRef.current;
    if (!article || !document.sections.length) return;
    const headings = Array.from(article.querySelectorAll("h2"));
    headings.forEach((heading, index) => {
      heading.id = `principle-${index + 1}`;
    });
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (visible) {
          const index = headings.indexOf(visible.target as HTMLHeadingElement);
          if (index >= 0) setActiveSection(index);
        }
      },
      { rootMargin: "-18% 0px -68% 0px", threshold: 0 },
    );
    headings.forEach((heading) => observer.observe(heading));
    return () => observer.disconnect();
  }, [document.sections]);

  const jumpToSection = (index: number) => {
    const heading = articleRef.current?.querySelectorAll("h2")[index];
    heading?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const pageContent = loading ? (
    <LoadingLine label={t("Loading")} />
  ) : content ? (
    <div className="methodology-page">
      <section className="methodology-hero">
        <div className="methodology-hero-meta">
          <span>ASTERMEM / {t("Methodology").toUpperCase()}</span>
          <span>{String(document.sections.length).padStart(2, "0")} {t("Principles").toUpperCase()}</span>
        </div>
        <div className="methodology-hero-grid">
          <div>
            <span className="methodology-edition">MANIFESTO · 2026</span>
            <h1>{document.title}</h1>
          </div>
          <div className="methodology-lede">
            <Markdown source={document.intro} />
            <button type="button" onClick={() => jumpToSection(0)}>
              {t("Read the principles")}
              <IconArrowDown aria-hidden="true" />
            </button>
          </div>
        </div>
      </section>

      <section className="methodology-system" aria-label={t("The AsterMem system")}>
        <div className="methodology-system-head">
          <span className="kicker">THE SYSTEM</span>
          <strong>{t("From source truth to useful context")}</strong>
        </div>
        <div className="methodology-system-flow">
          <article>
            <span>01 / SOURCE</span>
            <strong>{t("Original text")}</strong>
            <p>{t("Markdown stays authoritative.")}</p>
          </article>
          <IconArrowRight aria-hidden="true" />
          <article>
            <span>02 / ENGINE</span>
            <strong>{t("Memory engine")}</strong>
            <p>{t("Chunk, retrieve, audit, consolidate.")}</p>
          </article>
          <IconArrowRight aria-hidden="true" />
          <article>
            <span>03 / CONTEXT</span>
            <strong>{t("Agent context")}</strong>
            <p>{t("Only the right memory, when needed.")}</p>
          </article>
        </div>
      </section>

      <div className="methodology-reading-layout">
        <aside className="methodology-index">
          <div>
            <span className="kicker">{t("Principle index")}</span>
            <span className="methodology-live-source">{t("Live from Markdown")}</span>
          </div>
          <nav aria-label={t("Principle index")}>
            {document.sections.map((section, index) => (
              <button
                type="button"
                key={section}
                className={activeSection === index ? "active" : ""}
                onClick={() => jumpToSection(index)}
              >
                <em>{String(index + 1).padStart(2, "0")}</em>
                <span>{section}</span>
              </button>
            ))}
          </nav>
        </aside>

        <article className="methodology-article" ref={articleRef}>
          <Markdown source={document.body} />
        </article>
      </div>

      <footer className="methodology-closing">
        <span className="kicker">ASTERMEM / MEMORY INFRASTRUCTURE</span>
        <strong>{t("You provide the memory material. AI remembers who you are.")}</strong>
        <span>{t("Local-first. Traceable. Built for agents.")}</span>
      </footer>
    </div>
  ) : (
    <EmptyState message={t("Unable to load the methodology document")} />
  );

  if (authenticated === false) {
    return (
      <div className="landing-page methodology-public">
        <header className="landing-nav">
          <Link to="/" className="landing-logo" aria-label="AsterMem">
            <img src="/astermem-icon.png" alt="" aria-hidden="true" />
            <span>ASTERMEM</span>
          </Link>
          <nav aria-label={t("Landing navigation")}>
            <Link to="/">{t("Home")}</Link>
            <LocaleSwitcher />
            <Link to="/login" className="landing-nav-cta">
              {t("Sign in")}
            </Link>
          </nav>
        </header>
        <main className="methodology-public-body">
          {pageContent}
        </main>
        <footer className="landing-footer">
          <span>ASTERMEM</span>
          <span>© 2026 ASTEROVE</span>
        </footer>
      </div>
    );
  }

  if (authenticated === null) {
    return <LoadingLine label={t("Loading")} />;
  }

  return (
    <Layout title={t("Methodology")}>
      {pageContent}
    </Layout>
  );
}
