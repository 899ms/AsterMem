/**
 * Background: Home dashboard—the first page users see after logging in.
 * Design intent: Clean data cards showing system status (total memories, tag count, vectorization progress),
 * with feature module descriptions below to guide users through getting started.
 * Key constraint: Stats from /api/stats; feature descriptions go through i18n; avoid information overload.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { IconArrowRight, IconBrandX, IconCopy } from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { useI18n } from "../i18n";
import { useAuthSnapshot } from "../authState";
import { SOURCE_URL, X_HANDLE, X_URL } from "../license";
import { api } from "../api";
import { emitToast } from "../toast";
import { copyText } from "../clipboard";

interface Stats {
  total?: number;
  active?: number;
  tags?: string[];
  vector_count?: number;
  trunk_total?: number;
  trunk_ready?: number;
}

export function HomePage() {
  const { t } = useI18n();
  const { demoMode } = useAuthSnapshot();
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    api<Stats>("GET", "/api/stats")
      .then((res) => setStats(res ?? {}))
      .catch(() => setStats({}));
  }, []);

  /**
   * Background: The deployment guide lives in the repo (skill/deploy-astermem/SKILL.md), and the AI
   * doing the deployment runs on the user's machine, which already has the project checked out.
   * Design intent: One click copies a self-contained brief—where the guide is (local folder first,
   * repository as fallback), what to ask the user for, and what "done" looks like—so the user can
   * paste it into any agent app without hunting for files.
   */
  const copyDeployBrief = async () => {
    const brief = t(
      "Deploy AsterMem for me, following the deployment guide that ships with the project.\n\n1. Read the guide first: skill/deploy-astermem/SKILL.md inside my local AsterMem folder (the project currently running at {baseUrl}). If you cannot find the folder, fetch the same file from {repo}.\n2. Start by asking me how I want to deploy: a cloud server, or a machine I already own (this computer, a NAS, a Raspberry Pi) exposed through Cloudflare Tunnel with no public IP. Then follow the guide end to end and do the work yourself.\n3. Once it is live, remind me to change the default admin password, and migrate my existing memories by copying my local data/ directory over if needed.\n\nAsk me only for credentials and decisions. When finished, report the final URL and the completion checklist.",
      { baseUrl: window.location.origin, repo: SOURCE_URL },
    );
    if (await copyText(brief)) emitToast("success", t("Instructions copied"));
    else emitToast("error", t("Copy failed, select the text manually"));
  };

  const features = demoMode
    ? [
        {
          title: t("Memories"),
          desc: t("Browse the sample library and pull content back with semantic search."),
          to: "/memories",
        },
        {
          title: t("Graph"),
          desc: t("View your memories in a knowledge graph, timeline, or vector space."),
          to: "/graph",
        },
        {
          title: t("Tags"),
          desc: t("Organize memories with a tag tree and filter them faster."),
          to: "/tags",
        },
        {
          title: t("Methodology"),
          desc: t("Why original text is the only truth, and how retrieval navigates it."),
          to: "/methodology",
        },
      ]
    : [
    {
      title: t("Memories"),
      desc: t("Open the memory library, keep writing, or pull content back with semantic search."),
      to: "/memories",
    },
    {
      title: t("Explore"),
      desc: t("Ask the memory library directly. AI brings the related context into its answer."),
      to: "/explore",
    },
    {
      title: t("Graph"),
      desc: t("View your memories in a knowledge graph, timeline, or vector space."),
      to: "/graph",
    },
    {
      title: t("Import / Export"),
      desc: t("Import text, files, and bookmarks directly. Export a backup when you need one."),
      to: "/import",
    },
    {
      title: t("Tags"),
      desc: t("Organize memories with a tag tree and filter them faster."),
      to: "/tags",
    },
    {
      title: t("Settings"),
      desc: t("Configure embedding and chat providers, then test the connection."),
      to: "/settings",
    },
  ];

  return (
    <Layout title={t("Home")}>
      <section className="home-hero">
        <h2 className="home-headline">
          {demoMode ? t("Have a look around.") : t("What do you want to find today?")}
        </h2>
        <p className="home-sub">
          {demoMode
            ? t("This is a read-only demo with a sample library. Nothing you do here is saved. Run your own instance to keep your memories.")
            : t("Your memories are here. Keep writing, search the library, ask AI, or open the graph.")}
        </p>
      </section>

      {demoMode && (
        <a className="home-ai-banner" href={SOURCE_URL} target="_blank" rel="noreferrer">
          <span className="kicker">ASTERMEM / SELF-HOSTED</span>
          <div>
            <h2>{t("Run it on your own machine")}</h2>
            <p>{t("AsterMem is free software. Clone the repository, start it with one command, and your memories never leave your disk.")}</p>
          </div>
          <span className="home-ai-banner-link">
            {t("Source code")}
            <IconArrowRight aria-hidden="true" />
          </span>
        </a>
      )}

      {demoMode && (
        <a className="home-ai-banner contact" href={X_URL} target="_blank" rel="noreferrer">
          <span className="kicker">ASTERMEM / CONTACT</span>
          <div>
            <h2>{t("Questions? Ask us on X")}</h2>
            <p>{t("This demo answers to nobody: there are no accounts and no inbox. If something here is unclear, broken, or you want to know where AsterMem is headed, write to {handle} on X and you will reach the people who build it.", { handle: X_HANDLE })}</p>
          </div>
          <span className="home-ai-banner-link">
            <IconBrandX aria-hidden="true" />
            {X_HANDLE}
          </span>
        </a>
      )}

      {!demoMode && (
        <Link to="/new" className="home-ai-banner">
          <span className="kicker">ASTERMEM SKILL / AI FIRST</span>
          <div>
            <h2>{t("Operating it? Hand it to AI.")}</h2>
            <p>{t("Works with nearly every AI agent app: Claude Code, Codex, Cursor, and more. Install the Skill once and they all share one memory brain, with memories, providers, and system settings handled through the API.")}</p>
          </div>
          <span className="home-ai-banner-link">
            {t("Set up AI")}
            <IconArrowRight aria-hidden="true" />
          </span>
        </Link>
      )}

      {!demoMode && (
        <button type="button" className="home-ai-banner deploy" onClick={copyDeployBrief}>
          <span className="kicker">ASTERMEM DEPLOY / AI FIRST</span>
          <div>
            <h2>{t("Deploying it? Hand it to AI.")}</h2>
            <p>{t("Take AsterMem live on a cloud server, or on a machine you already own — even without a public IP, through Cloudflare Tunnel. A deployment guide written for AI ships with the project: your AI asks how you want to deploy, then does the rest itself, from Docker and HTTPS to go-live hardening.")}</p>
          </div>
          <span className="home-ai-banner-link">
            {t("Copy instructions for AI")}
            <IconCopy aria-hidden="true" />
          </span>
        </button>
      )}

      {stats && (
        <section className="home-stats">
          <div className="stat-card">
            <span className="stat-value">{stats.active ?? stats.total ?? "—"}</span>
            <span className="stat-label">{t("Memories")}</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{stats.tags?.length ?? "—"}</span>
            <span className="stat-label">{t("Tags")}</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">
              {stats.vector_count != null ? stats.vector_count : "—"}
            </span>
            <span className="stat-label">{t("Vectorized")}</span>
          </div>
        </section>
      )}

      <section className="home-features">
        {features.map((f) => (
          <Link key={f.to} to={f.to} className="feature-card">
            <h3>{f.title}</h3>
            <p>{f.desc}</p>
          </Link>
        ))}
      </section>
    </Layout>
  );
}
